from decimal import Decimal, InvalidOperation

from django import template

from pretix_autoconfig.signals_combo_voucher import is_combo_voucher as _is_combo_voucher
from pretix_autoconfig.signals_config import get_hidden_question_prefixes
from pretix_autoconfig.signals_fee import get_fee_min, get_fee_percent
from pretix_autoconfig.signals_referral import load_referral_code, load_referral_voucher

register = template.Library()


@register.filter
def is_combo_voucher(voucher):
    """True for a synthesised private-link + discount-code voucher (code ``CMB-…``).

    Its code is regenerated per checkout, so templates must show "discount applied"
    without a code for it.
    """
    return _is_combo_voucher(voucher)


@register.filter
def grouped_positions(positions):
    """Aggregate cart positions by (item, variation, unit_price) into summary rows.

    Returns a list of dicts with keys: item, variation, unit_price, count, total.
    Price is included in the grouping key so positions with different prices (e.g.
    grandfathered early-bird slots) stay on separate rows.
    """
    groups = {}
    order = []
    for pos in positions:
        variation_pk = pos.variation.pk if pos.variation else None
        key = (pos.item.pk, variation_pk, pos.price)
        count = getattr(pos, "count", None) or 1
        if key not in groups:
            groups[key] = {
                "item": pos.item,
                "variation": pos.variation,
                "unit_price": pos.price,
                "count": 0,
                "total": Decimal("0"),
            }
            order.append(key)
        groups[key]["count"] += count
        groups[key]["total"] += pos.price * count
    return [groups[k] for k in order]


@register.simple_tag
def cart_discount_state(cart):
    """How much of the cart a discount covers, for the Review page's voucher card.

    ``voucher`` is the first discounting voucher (``price_mode`` other than "none"),
    ``applied`` / ``total`` count tickets, not grouped rows. On a private link, tickets
    added after the code was applied still carry only the link's no-discount voucher, so
    ``applied < total`` and the page offers the promo box again.
    """
    positions = (cart.get("positions") if isinstance(cart, dict) else getattr(cart, "positions", None)) or []
    state = {"voucher": None, "applied": 0, "total": 0}
    for pos in positions:
        count = getattr(pos, "count", None) or 1
        state["total"] += count
        voucher = pos.voucher
        if voucher is not None and voucher.price_mode != "none":
            state["applied"] += count
            state["voucher"] = state["voucher"] or voucher
    return state


def _decimal_or_zero(value):
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


@register.simple_tag
def autoconfig_fee_percent():
    value = get_fee_percent()
    return str(value if value is not None else Decimal("0"))


@register.simple_tag
def autoconfig_fee_min():
    value = get_fee_min()
    return str(value if value is not None else Decimal("0"))


@register.simple_tag
def autoconfig_fee_for_amount(amount):
    base_amount = _decimal_or_zero(amount)
    fee_percent = _decimal_or_zero(get_fee_percent())
    fee_min = _decimal_or_zero(get_fee_min())
    if fee_percent <= 0 and fee_min <= 0:
        return "0"

    fee = (base_amount * fee_percent) / Decimal("100")
    if fee < fee_min:
        fee = fee_min
    return str(fee)


@register.simple_tag
def autoconfig_effective_fee_percent(event):
    value = get_fee_percent()
    return str(value if value is not None else Decimal("0"))


@register.simple_tag
def autoconfig_effective_fee_min(event):
    value = get_fee_min()
    return str(value if value is not None else Decimal("0"))


@register.simple_tag(takes_context=True)
def referral_voucher_code(context):
    """Return the referral voucher code stored in session, or empty string."""
    request = context.get("request")
    event = context.get("event")
    if request is None or event is None:
        return ""
    return load_referral_voucher(request, event)


@register.simple_tag(takes_context=True)
def shop_return_url(context):
    """The shop URL the buyer arrived on, for "back to the shop" links.

    Carries the ``voucher`` and ``ref`` that came in the arrival URL (stored in the session
    by ``capture_referral_on_page_load``). Without them a private-link buyer lands on the
    public view: their sold-out tickets show "in your cart" and "SOLD OUT" with no controls.
    A code typed into the promo box is POSTed and never replaces the arrival voucher.
    """
    from urllib.parse import urlencode

    from pretix.multidomain.urlreverse import eventreverse

    request = context.get("request")
    event = context.get("event") or getattr(request, "event", None)
    if event is None:
        return ""
    url = eventreverse(event, "presale:event.index")
    if request is None:
        return url
    params = {
        k: v
        for k, v in (("voucher", load_referral_voucher(request, event)), ("ref", load_referral_code(request, event)))
        if v
    }
    return f"{url}?{urlencode(params)}" if params else url


@register.simple_tag(takes_context=True)
def referral_voucher_discount_pct(context):
    """Return the whole-number percent discount for the session referral voucher, or '0'."""
    request = context.get("request")
    event = context.get("event")
    if not request or not event:
        return "0"
    code = load_referral_voucher(request, event)
    if not code:
        return "0"
    val = event.vouchers.filter(code__iexact=code, price_mode="percent").values_list("value", flat=True).first()
    return str(int(round(float(val)))) if val is not None else "0"


@register.simple_tag(takes_context=True)
def event_vouchers_exist(context):
    """Return True if the event has any active vouchers."""
    event = context.get("event")
    if not event:
        return False
    return event.vouchers.exists()


@register.simple_tag(takes_context=True)
def autoconfig_powered_by(context):
    """Structured powered-by footer HTML."""
    from django.urls import reverse
    from django.utils.html import format_html
    from django.utils.safestring import mark_safe
    from django.utils.translation import gettext as _
    from pretix.base.settings import GlobalSettingsObject

    request = context.get("request")
    gs = GlobalSettingsObject()
    d = gs.settings.license_check_input or {}

    name = d.get("poweredby_name", "")
    brand_url = d.get("poweredby_url", "")
    pretix_url = "https://pretix.eu"

    source_part = mark_safe("")
    if d.get("base_license") == "agpl" and request:
        try:
            source_url = request.build_absolute_uri(reverse("source"))
            source_part = format_html(
                ' (<a href="{}" target="_blank" rel="noopener">{}</a>)',
                source_url,
                _("source code"),
            )
        except Exception:
            pass

    pretix_part = format_html(
        '<a href="{}" target="_blank" rel="noopener">based on pretix</a>',
        pretix_url,
    )

    if name and brand_url:
        return format_html(
            '<span class="autoconfig-footer-poweredby">'
            '<span class="autoconfig-footer-poweredby__label">Powered by</span>'
            '<span class="autoconfig-footer-poweredby__logo">'
            '<a href="{}" target="_blank" rel="noopener">'
            '<span class="autoconfig-footer-logo">{}</span>'
            "</a></span>"
            '<span class="autoconfig-footer-poweredby__line2">{}{}</span>'
            "</span>",
            brand_url,
            name,
            pretix_part,
            source_part,
        )
    return format_html(
        '<span class="autoconfig-footer-poweredby">'
        '<span class="autoconfig-footer-poweredby__line2">'
        '<a href="{}" target="_blank" rel="noopener">ticketing powered by pretix</a>'
        "{}</span>"
        "</span>",
        pretix_url,
        source_part,
    )


@register.simple_tag
def autoconfig_email_poweredby():
    """Plain-text-safe powered-by line for email footer."""
    from django.utils.html import format_html
    from pretix.base.settings import GlobalSettingsObject

    gs = GlobalSettingsObject()
    d = gs.settings.license_check_input or {}
    name = d.get("poweredby_name", "")
    brand_url = d.get("poweredby_url", "")
    pretix_url = "https://pretix.eu"

    if name and brand_url:
        return format_html(
            'Powered by <a href="{}" target="_blank" rel="noopener">{}</a>'
            ' based on <a href="{}" target="_blank" rel="noopener">pretix</a>',
            brand_url,
            name,
            pretix_url,
        )
    return format_html(
        'Ticketing powered by <a href="{}" target="_blank" rel="noopener">pretix</a>',
        pretix_url,
    )


@register.filter
def is_platform_question(identifier):
    """True if this question is managed by the platform and should not be re-displayed.

    Configured through ``hidden_question_prefixes``.
    """
    if not identifier:
        return False
    return any(str(identifier).startswith(prefix) for prefix in get_hidden_question_prefixes())
