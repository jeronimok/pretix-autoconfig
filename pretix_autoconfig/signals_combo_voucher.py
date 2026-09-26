"""Combine a discount code with a sold-out-bypass private link.

A private link puts a ``price_mode="none"`` + ``allow_ignore_quota`` voucher, tagged
``private:<id>``, on every cart line. Pretix allows only one voucher per line, so the buyer
cannot also redeem a discount code. When they enter one on such a cart we synthesise a
single voucher that carries BOTH the discount and ``allow_ignore_quota``, swap it onto the
cart lines, and mirror the redemption onto the two real vouchers (the link's and the
code's) as the order moves through its lifecycle -- so their ``max_usages`` stay enforced
and their counters stay truthful.

The synthesised voucher is tagged ``private:<id>:combo:<code>`` and its code is
``CMB-<random>``; the latter must never be shown to the buyer (it is regenerated per
checkout). Templates render "N% discount applied" without a code for such vouchers.
"""

import json
import logging
from collections import Counter

from django.db.models import F
from django.utils.crypto import get_random_string
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

COMBO_SEP = ":combo:"
SYNTH_PREFIX = "CMB-"
_META_COUNTED = "autoconfig_combo_counted"
_META_REVERTED = "autoconfig_combo_reverted"


def is_combo_voucher(voucher):
    """True for a synthesised discount+bypass voucher created by this module."""
    if not voucher:
        return False
    return (voucher.code or "").startswith(SYNTH_PREFIX) and COMBO_SEP in (voucher.tag or "")


def _parse_combo_tag(tag):
    """`private:<id>:combo:<code>` -> (`private:<id>`, `<code>`). None on mismatch."""
    parts = (tag or "").split(":")
    if len(parts) != 4 or parts[0] != "private" or parts[2] != "combo":
        return None
    return f"private:{parts[1]}", parts[3]


# ---------------------------------------------------------------------------
# Cart: intercept voucher redemption on a bypass-private-link cart
# ---------------------------------------------------------------------------


def patch_cart_apply_voucher():
    from pretix.base.models import Voucher
    from pretix.base.services.cart import CartError, CartManager
    from pretix.base.services.pricing import get_listed_price

    if getattr(CartManager, "_autoconfig_combo_patched", False):
        return
    _orig = CartManager.apply_voucher

    def apply_voucher(self, voucher_code):
        try:
            entered = self.event.vouchers.get(code__iexact=voucher_code.strip())
        except Voucher.DoesNotExist:
            return _orig(self, voucher_code)

        if (entered.tag or "").startswith("private:") or not entered.is_active():
            return _orig(self, voucher_code)

        positions = list(self.positions)
        if not positions:
            return _orig(self, voucher_code)

        def is_bypass(p):
            v = p.voucher
            return bool(
                p.voucher_id
                and v
                and (v.tag or "").startswith("private:")
                and COMBO_SEP not in (v.tag or "")
                and v.price_mode == "none"
            )

        def combined_as(p):
            """(link_tag, code) for a line already combined here, else None."""
            return _parse_combo_tag(p.voucher.tag) if is_combo_voucher(p.voucher) else None

        # Lines added after an earlier combine are still plain bypass lines. The buyer
        # re-enters the code to discount them; lines already combined stay as they are.
        open_lines = [p for p in positions if is_bypass(p)]
        combined = [c for c in (combined_as(p) for p in positions) if c]
        if not open_lines or len(open_lines) + len(combined) != len(positions):
            return _orig(self, voucher_code)
        if len({p.voucher.tag for p in open_lines} | {link for link, _ in combined}) != 1:
            return _orig(self, voucher_code)
        if any(code != entered.code for _, code in combined):
            raise CartError(_("Only one promotional code per order."))
        if not all(entered.applies_to(p.item, p.variation) for p in open_lines):
            return _orig(self, voucher_code)
        positions = open_lines

        priv = positions[0].voucher
        link_tag = priv.tag  # "private:<id>"
        now = self.real_now_dt

        # In-flight combines on the same parents: count live cart positions holding a
        # synthesised voucher, NOT the synthesised vouchers themselves. An abandoned
        # combine leaves an orphan CMB- voucher until its valid_until (the link's
        # expiry); counting those would hold a slot for weeks and soft-lock a
        # single-use link. Once a cart expires its positions are gone; once an order
        # is placed the count has moved onto the parent's `redeemed` via the mirror,
        # so neither is double-counted here.
        from pretix.base.models import CartPosition

        link_pending = (
            CartPosition.objects.filter(
                event=self.event, voucher__tag__startswith=link_tag + COMBO_SEP, expires__gt=now
            )
            .exclude(cart_id=self.cart_id)
            .count()
        )
        code_pending = (
            CartPosition.objects.filter(
                event=self.event,
                voucher__code__startswith=SYNTH_PREFIX,
                voucher__tag__endswith=COMBO_SEP + entered.code,
                expires__gt=now,
            )
            .exclude(cart_id=self.cart_id)
            .count()
        )
        # On a re-entry this cart's already-combined lines use the link and the code too;
        # `*_pending` excludes this cart, so count them here or a single-use code goes twice.
        link_remaining = priv.max_usages - priv.redeemed - link_pending - len(combined)
        code_remaining = entered.max_usages - entered.redeemed - code_pending - len(combined)
        need = len(positions)
        cap = min(link_remaining, code_remaining, need)
        if cap < need:
            if combined:
                raise CartError(_("This code can’t be applied to your new tickets. They stay at the full price."))
            raise CartError(_("This code can’t be combined with your access link for all of your tickets right now."))

        valids = [d for d in (entered.valid_until, priv.valid_until) if d]
        synth = Voucher.objects.create(
            event=self.event,
            code=SYNTH_PREFIX + get_random_string(16).upper(),
            price_mode=entered.price_mode,
            value=entered.value,
            item=entered.item,
            variation=entered.variation,
            quota=entered.quota,
            allow_ignore_quota=True,
            block_quota=False,
            max_usages=cap,
            valid_until=min(valids) if valids else None,
            tag=link_tag + COMBO_SEP + entered.code,
            comment=f"auto: {entered.code} combined with {priv.code} at checkout",
        )

        vdiff = Counter()
        for p in positions:
            listed = p.listed_price
            if listed is None:
                listed = get_listed_price(p.item, p.variation, p.subevent)
            self._operations.append(self.VoucherOperation(p, synth, synth.calculate_price(listed)))
            vdiff[synth] += 1
        self._voucher_use_diff += vdiff

    CartManager.apply_voucher = apply_voucher
    CartManager._autoconfig_combo_patched = True


# ---------------------------------------------------------------------------
# The link's limit must see combined lines too
# ---------------------------------------------------------------------------
#
# Pretix limits a voucher by `max_usages - redeemed - cart lines holding that exact voucher`.
# Combined lines hold a CMB- voucher, so while they sit in a cart the link's PRIV- voucher
# looks untouched and another buyer (or the same one, from the plain shop page) could add
# the link's tickets again. Only a placed order moves them onto PRIV-.redeemed (the mirror
# above). Spec 2026-09-23-private-link-combined-cap.


def _is_plain_link(voucher):
    tag = (voucher.tag or "") if voucher else ""
    return tag.startswith("private:") and COMBO_SEP not in tag


def _link_tag_of(voucher):
    """`private:<id>` for a link voucher or a line combined from one, else None."""
    if _is_plain_link(voucher):
        return voucher.tag
    parsed = _parse_combo_tag(voucher.tag) if is_combo_voucher(voucher) else None
    return parsed[0] if parsed else None


def combined_link_lines(event, link_tag, now_dt, reviving_position_ids=()):
    """Cart lines, in any cart, that were combined from the link `link_tag`: live ones, plus
    expired ones Pretix is reviving in this very operation.

    Pretix leaves the lines it revives out of its own count because it counts them under
    their own voucher. For a combined line that is the CMB- voucher, not the link's, so here
    they must be counted, or re-opening an expired combined cart and adding a ticket in one
    go slips past the link's limit.
    """
    from django.db.models import Q
    from pretix.base.models import CartPosition

    return (
        CartPosition.objects.filter(event=event, voucher__tag__startswith=link_tag + COMBO_SEP)
        .filter(Q(expires__gte=now_dt) | Q(pk__in=list(reviving_position_ids)))
        .count()
    )


def patch_voucher_availability():
    """Subtract combined lines from a plain link voucher's availability at add-to-cart time.

    Wraps Pretix's module-level `_get_voucher_availability`, which `CartManager` calls by
    module name and `pretix.api.views.cart` imports by name. Every other voucher gets
    Pretix's own result unchanged. Marking the link voucher cart-dependent makes Pretix
    refuse with its "locked in a cart" wording, which the shop already rewords.
    """
    from pretix.base.services import cart as cart_service

    orig = getattr(cart_service, "_get_voucher_availability", None)
    if orig is None:
        logger.warning("pretix.base.services.cart._get_voucher_availability not found; link cap not extended")
        return
    if getattr(orig, "_autoconfig_combined_cap", False):
        return

    def _get_voucher_availability(event, voucher_use_diff, now_dt, exclude_position_ids):
        vouchers_ok, depend_on_cart = orig(event, voucher_use_diff, now_dt, exclude_position_ids)
        for voucher in list(vouchers_ok):
            if not _is_plain_link(voucher):
                continue
            held = combined_link_lines(event, voucher.tag, now_dt, exclude_position_ids)
            if held:
                vouchers_ok[voucher] -= held
                depend_on_cart.add(voucher)
        return vouchers_ok, depend_on_cart

    _get_voucher_availability._autoconfig_combined_cap = True
    cart_service._get_voucher_availability = _get_voucher_availability
    try:
        from pretix.api.views import cart as api_cart

        if getattr(api_cart, "_get_voucher_availability", None) is orig:
            api_cart._get_voucher_availability = _get_voucher_availability
    except ImportError:
        pass


def on_validate_cart(sender, positions=None, **kwargs):
    """Checkout backstop: refuse when the cart holds more link tickets than the link has left.

    Pretix sends `validate_cart` on every checkout step, including the "Pay" submit. Its own
    order-placement check counts only lines on the PRIV- voucher itself, so this is where a
    cart that got past the add-to-cart check another way is stopped.
    """
    from django.db.models import Q
    from django.utils.timezone import now as tz_now
    from pretix.base.models import CartPosition
    from pretix.base.services.cart import CartError

    positions = list(positions or [])
    own = Counter(tag for tag in (_link_tag_of(p.voucher) for p in positions if p.voucher_id) if tag)
    if not own:
        return
    cart_id = positions[0].cart_id
    for link_tag, count in own.items():
        link = sender.vouchers.filter(tag=link_tag).first()
        if link is None:
            continue
        elsewhere = (
            CartPosition.objects.filter(event=sender, expires__gte=tz_now())
            .filter(Q(voucher__tag=link_tag) | Q(voucher__tag__startswith=link_tag + COMBO_SEP))
            .exclude(cart_id=cart_id)
            .count()
        )
        if link.redeemed + elsewhere + count > link.max_usages:
            raise CartError(
                _("This link has fewer tickets left than your cart holds. Please remove some and try again.")
            )


# ---------------------------------------------------------------------------
# Orders: mirror the redemption onto the real link + code vouchers
# ---------------------------------------------------------------------------


def _combo_groups(order):
    """{(link_tag, code): n} over this order's positions that carry a combo voucher."""
    groups = Counter()
    for p in order.positions.select_related("voucher"):
        if not is_combo_voucher(p.voucher):
            continue
        parsed = _parse_combo_tag(p.voucher.tag)
        if parsed:
            groups[parsed] += 1
    return groups


def _bump(event, link_tag, code, delta):
    from pretix.base.models import Voucher

    link_v = event.vouchers.filter(tag=link_tag).first()
    code_v = event.vouchers.filter(code=code).first()
    ids = [v.pk for v in (link_v, code_v) if v]
    if ids:
        Voucher.objects.filter(pk__in=ids).update(redeemed=F("redeemed") + delta)


def _write_meta(order, md):
    order.meta_info = json.dumps(md)
    order.save(update_fields=["meta_info"])
    try:
        del order.meta_info_data  # bust cached_property
    except AttributeError:
        pass


def on_order_placed(sender, order, **kwargs):
    md = order.meta_info_data or {}
    if _META_COUNTED in md:
        return
    groups = _combo_groups(order)
    if not groups:
        return
    recorded = []
    for (link_tag, code), n in groups.items():
        _bump(sender, link_tag, code, n)
        recorded.append([link_tag, code, n])
    md[_META_COUNTED] = recorded
    _write_meta(order, md)


def _revert(sender, order):
    md = order.meta_info_data or {}
    if _META_COUNTED not in md or _META_REVERTED in md:
        return
    for link_tag, code, n in md[_META_COUNTED]:
        _bump(sender, link_tag, code, -n)
    md[_META_REVERTED] = True
    _write_meta(order, md)


def on_order_canceled(sender, order, **kwargs):
    _revert(sender, order)


def on_order_expired(sender, order, **kwargs):
    _revert(sender, order)


def on_order_reactivated(sender, order, **kwargs):
    md = order.meta_info_data or {}
    if _META_REVERTED not in md:
        return
    for link_tag, code, n in md.get(_META_COUNTED, []):
        _bump(sender, link_tag, code, n)
    del md[_META_REVERTED]
    _write_meta(order, md)


def connect_signals():
    from pretix.base.signals import (
        order_canceled,
        order_expired,
        order_placed,
        order_reactivated,
        validate_cart,
    )

    order_placed.connect(on_order_placed, dispatch_uid="autoconfig_combo_order_placed", weak=False)
    order_canceled.connect(on_order_canceled, dispatch_uid="autoconfig_combo_order_canceled", weak=False)
    order_expired.connect(on_order_expired, dispatch_uid="autoconfig_combo_order_expired", weak=False)
    order_reactivated.connect(on_order_reactivated, dispatch_uid="autoconfig_combo_order_reactivated", weak=False)
    validate_cart.connect(on_validate_cart, dispatch_uid="autoconfig_combo_validate_cart", weak=False)
