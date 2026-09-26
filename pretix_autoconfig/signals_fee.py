import os
from decimal import Decimal, InvalidOperation

from django.dispatch import receiver
from pretix.base.decimal import round_decimal
from pretix.base.models import TaxRule
from pretix.base.models.orders import OrderFee
from pretix.base.signals import order_fee_calculation
from pretix.presale.signals import fee_calculation_for_cart


def _get_config(env_var, section, key):
    """Read a value from env var or pretix.cfg, return None if not set."""
    value = os.environ.get(env_var)
    if value:
        return value
    try:
        from pretix.settings import config

        return config.get(section, key, fallback=None) or None
    except Exception:
        return None


def get_fee_percent():
    value = _get_config("PRETIX_AUTOCONFIG_FEE_PERCENT", "pretix_autoconfig", "fee_percent")
    try:
        return Decimal(str(value)) if value else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def get_fee_min():
    value = _get_config("PRETIX_AUTOCONFIG_FEE_MIN", "pretix_autoconfig", "fee_min")
    try:
        return Decimal(str(value)) if value else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def _fee_base_price(position):
    """Return the pre-discount ticket price to use as the fee base.

    Referral discounts (percent vouchers) should reduce only the ticket price,
    not the service fee. We use the original listed price when available.

    CartPosition exposes listed_price directly. OrderPosition stores the
    discount amount in voucher_budget_use, so we reconstruct the original price
    by adding it back.
    """
    voucher = getattr(position, "voucher", None)
    if voucher and getattr(voucher, "price_mode", None) == "percent":
        listed = getattr(position, "listed_price", None)
        if listed is not None:
            return listed
        budget_use = getattr(position, "voucher_budget_use", None) or Decimal("0")
        return position.gross_price_before_rounding + budget_use
    return position.gross_price_before_rounding


def _calculate_autoconfig_fee(event, total, positions=None):
    if not total or total <= Decimal("0"):
        return []

    fee_percent = get_fee_percent()
    fee_min = get_fee_min()
    if not fee_percent and not fee_min:
        return []

    fee_percent = fee_percent or Decimal("0")
    fee_min = fee_min or Decimal("0")

    if positions:
        fee = Decimal("0")
        for position in positions:
            pos_price = _fee_base_price(position)
            if not pos_price or pos_price <= Decimal("0"):
                continue
            ticket_fee = round_decimal(pos_price * fee_percent / Decimal("100"), event.currency)
            if fee_min and ticket_fee < fee_min:
                ticket_fee = fee_min
            fee += ticket_fee
    else:
        fee = round_decimal(total * fee_percent / Decimal("100"), event.currency)
        if fee_min and fee < fee_min:
            fee = fee_min

    if not fee or fee <= Decimal("0"):
        return []

    tax_rule = event.cached_default_tax_rule or TaxRule.zero()
    tax = tax_rule.tax(fee, base_price_is="gross")

    return [
        OrderFee(
            fee_type=OrderFee.FEE_TYPE_SERVICE,
            internal_type="",
            value=fee,
            tax_rate=tax.rate,
            tax_code=getattr(tax, "code", ""),
            tax_value=tax.tax,
            tax_rule=tax_rule,
        )
    ]


@receiver(fee_calculation_for_cart, dispatch_uid="autoconfig_fee_calc_cart")
def autoconfig_cart_fee(sender, invoice_address, total, positions=None, **kwargs):
    return _calculate_autoconfig_fee(sender, total, positions)


@receiver(order_fee_calculation, dispatch_uid="autoconfig_fee_calc_order")
def autoconfig_order_fee(sender, invoice_address, total, positions=None, **kwargs):
    return _calculate_autoconfig_fee(sender, total, positions)
