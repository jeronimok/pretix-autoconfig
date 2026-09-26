"""Session-scoped JSON view of the buyer's own cart.

Single source of truth for `live_cart.js`. The template variable it used to read
(`cart.positions`) is *grouped* by Pretix — one entry per item/price with a `.count`
(`pretix/presale/views/__init__.py`, "Group items of the same variation") — so a cart
holding three tickets re-rendered as a quantity of 1, and the next "+" synced the real
cart down to that wrong number. Reading quantities from here instead fixes that, and
also hands out the position ids needed to remove a single ticket without clearing (and
destroying) the whole cart.

The cart is resolved from the session only. There is deliberately no cart-id parameter:
this endpoint must never be able to return somebody else's cart.

It is mounted on the plugin's *root* urlpatterns rather than `event_patterns` on purpose.
`event_patterns` are wrapped in `require_plugin`, so they 404 on any event that does not
have pretix_autoconfig in its plugin list — but our shop templates override Pretix's for
every event regardless of that list, so the JS that needs this endpoint runs there too.
Gating the data source differently from the page that consumes it produced a 404 on
exactly the events that still render our cart UI.
"""

from django.http import Http404, JsonResponse
from django.views.decorators.http import require_GET


def _position_key(position):
    """The name of the quantity input this position belongs to, as Pretix renders it."""
    if position.variation_id:
        return f"variation_{position.item_id}_{position.variation_id}"
    return f"item_{position.item_id}"


def _resolve_event(request, organizer, event):
    from django_scopes import scope
    from pretix.base.models import Event

    if getattr(request, "event", None) is not None:
        return request.event
    with scope(organizer=None):
        try:
            ev = Event.objects.select_related("organizer").get(slug=event, organizer__slug=organizer)
        except Event.DoesNotExist:
            raise Http404()
    request.event = ev
    request.organizer = ev.organizer
    return ev


def _drain_stale_messages(request):
    """Consume messages queued by AJAX cart calls.

    Pretix's cart views push their outcome into the Django messages framework even when
    the caller is our JS, which never renders it. The message then waits in the session
    and appears on whatever page the buyer loads next — a stale "voucher is locked in a
    cart" surfacing on the checkout page of a buyer whose cart is perfectly fine. Every
    cart call is followed by a read of this endpoint, so draining here consumes exactly
    those. Messages meant for a page render are consumed by that render before this runs.
    """
    try:
        from django.contrib.messages import get_messages

        storage = get_messages(request)
        if storage is not None:
            for _ in storage:
                pass
    except Exception:
        pass


def _link_state(request, ev, cart_id):
    """Counts for the private link the buyer arrived on, or None.

    Lets `live_cart.js` word a refused change truthfully: "someone else is using this link"
    only when other carts really hold it, "all bought" when orders used it up. Only counts,
    never another cart's contents.
    """
    from django.db.models import Q
    from django.utils.timezone import now
    from pretix.base.models import CartPosition

    from .signals_combo_voucher import COMBO_SEP
    from .signals_referral import load_referral_voucher

    code = load_referral_voucher(request, ev)
    if not code:
        return None
    link = ev.vouchers.filter(code__iexact=code, tag__startswith="private:").exclude(tag__contains=COMBO_SEP).first()
    if link is None:
        return None
    others = CartPosition.objects.filter(event=ev, expires__gt=now()).filter(
        Q(voucher__tag=link.tag) | Q(voucher__tag__startswith=link.tag + COMBO_SEP)
    )
    if cart_id:
        others = others.exclude(cart_id=cart_id)
    return {"cap": link.max_usages, "redeemed": link.redeemed, "held_by_others": others.count()}


@require_GET
def cart_json(request, organizer=None, event=None, *args, **kwargs):
    from django_scopes import scope
    from pretix.base.models import CartPosition
    from pretix.base.services.cart import get_fees
    from pretix.presale.views import cached_invoice_address
    from pretix.presale.views.cart import get_or_create_cart_id

    ev = _resolve_event(request, organizer, event)
    _drain_stale_messages(request)

    empty = {
        "positions": [],
        "quantities": {},
        "total": "0.00",
        "currency": ev.currency,
        "expires": None,
    }

    cart_id = get_or_create_cart_id(request, create=False)
    with scope(organizer=ev.organizer):
        link = _link_state(request, ev, cart_id)
    if link is not None:
        empty["link"] = link
    if not cart_id:
        response = JsonResponse(empty)
        response["Cache-Control"] = "no-store"
        return response

    with scope(organizer=ev.organizer):
        positions = list(
            CartPosition.objects.filter(cart_id=cart_id, event=ev).select_related("item", "variation").order_by("pk")
        )
        if not positions:
            response = JsonResponse(empty)
            response["Cache-Control"] = "no-store"
            return response

        try:
            fees = get_fees(
                event=ev,
                request=request,
                invoice_address=cached_invoice_address(request),
                payments=None,
                positions=positions,
            )
        except Exception:
            # A fee plugin blowing up must not cost the buyer their quantities — the
            # ticket subtotal is still correct and is what the inputs are driven from.
            fees = []

    total = sum(p.price for p in positions) + sum(f.value for f in fees)

    quantities = {}
    for p in positions:
        key = _position_key(p)
        quantities[key] = quantities.get(key, 0) + 1

    expires = min(p.expires for p in positions)

    response = JsonResponse(
        {
            "positions": [
                {
                    "id": p.pk,
                    "key": _position_key(p),
                    "item": p.item_id,
                    "variation": p.variation_id,
                    "price": str(p.price),
                    "listed_price": str(p.listed_price if p.listed_price is not None else p.price),
                }
                for p in positions
            ],
            "quantities": quantities,
            "total": str(total),
            "currency": ev.currency,
            "expires": int(expires.timestamp()),
            **({"link": link} if link is not None else {}),
        }
    )
    # This is the client's single source of truth for what's actually reserved. A stale
    # cached copy — served from the browser's HTTP cache, or replayed on a back/forward
    # navigation — would show the wrong quantity while the real server cart differs,
    # and a naive "+" from there re-adds on top of what's already held: exactly the
    # self-inflicted voucher-locked-in-a-cart failure this spec exists to prevent.
    response["Cache-Control"] = "no-store"
    return response
