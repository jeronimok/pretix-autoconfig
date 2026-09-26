"""
Tests for the optimistic fee calculation in live_cart.js.

The JS `computeOptimisticTotal` must mirror `signals_fee._calculate_autoconfig_fee`.
These tests encode the expected values so any future drift between the two is caught.

Rounding rule: round to 2 decimal places immediately after every arithmetic
operation — never carry more than 2 dp in any intermediate value.

Each ticket is represented as (discounted_price, original_price, qty).
When original_price == discounted_price there is no voucher discount active.
"""

from decimal import ROUND_HALF_UP, Decimal


def _round2(x):
    """Mirror of JS roundTo2: round to 2 dp, half-up."""
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _js_fee(tickets, fee_percent, fee_min):
    """Python mirror of computeOptimisticTotal's fee logic.

    tickets: list of (discounted_price, original_price, qty)
    Fee is calculated on original_price (the <del> price when a percent
    voucher is active), rounded immediately, then accumulated with rounding.
    """
    fee = Decimal("0")
    any_fee = fee_percent > 0 or fee_min > 0
    for discounted_price, original_price, qty in tickets:
        if not any_fee or qty <= 0:
            continue
        fee_base = original_price
        ticket_fee = _round2(fee_base * fee_percent / Decimal("100"))
        if ticket_fee < fee_min:
            ticket_fee = fee_min
        fee = _round2(fee + ticket_fee * qty)
    return fee


def ticket(price, qty, original=None):
    """Helper: (discounted_price, original_price, qty). original defaults to price."""
    orig = Decimal(str(original)) if original is not None else Decimal(str(price))
    return (Decimal(str(price)), orig, qty)


FEE_PERCENT = Decimal("2.9")
FEE_MIN = Decimal("0.60")


class TestLiveCartFeeNoDiscount:
    """No referral discount — original price == discounted price."""

    def test_single_ticket_below_min(self):
        # 1 × €15: round(15×2.9%=0.435) = 0.44 → min 0.60 applies
        assert _js_fee([ticket(15, 1)], FEE_PERCENT, FEE_MIN) == Decimal("0.60")

    def test_two_tickets_below_min(self):
        # 2 × €15: ticket_fee=0.44 → min 0.60; fee = round(0 + 0.60×2) = 1.20
        assert _js_fee([ticket(15, 2)], FEE_PERCENT, FEE_MIN) == Decimal("1.20")

    def test_three_tickets_below_min(self):
        # 3 × €15: fee = round(0 + 0.60×3) = 1.80
        assert _js_fee([ticket(15, 3)], FEE_PERCENT, FEE_MIN) == Decimal("1.80")

    def test_single_ticket_above_min(self):
        # 1 × €25: round(25×2.9%=0.725) = 0.73 → above min
        assert _js_fee([ticket(25, 1)], FEE_PERCENT, FEE_MIN) == Decimal("0.73")

    def test_two_tickets_above_min(self):
        # 2 × €25: ticket_fee=0.73; fee = round(0 + 0.73×2) = 1.46
        assert _js_fee([ticket(25, 2)], FEE_PERCENT, FEE_MIN) == Decimal("1.46")

    def test_single_ticket_well_above_min(self):
        # 1 × €50: round(50×2.9%=1.45) = 1.45 → above min
        assert _js_fee([ticket(50, 1)], FEE_PERCENT, FEE_MIN) == Decimal("1.45")

    def test_two_tickets_well_above_min(self):
        # 2 × €50: ticket_fee=1.45; fee = round(0 + 1.45×2) = 2.90
        assert _js_fee([ticket(50, 2)], FEE_PERCENT, FEE_MIN) == Decimal("2.90")

    def test_mixed_ticket_types_both_below_min(self):
        # 1×€15 (min 0.60) + 1×€10: round(10×2.9%=0.29)=0.29 → min 0.60
        assert _js_fee([ticket(15, 1), ticket(10, 1)], FEE_PERCENT, FEE_MIN) == Decimal("1.20")

    def test_mixed_ticket_types_one_below_one_above(self):
        # 1×€15 (min 0.60) + 1×€25 (0.73): fee = round(0.60 + 0.73) = 1.33
        assert _js_fee([ticket(15, 1), ticket(25, 1)], FEE_PERCENT, FEE_MIN) == Decimal("1.33")

    def test_zero_qty_skipped(self):
        assert _js_fee([ticket(15, 0)], FEE_PERCENT, FEE_MIN) == Decimal("0")

    def test_no_fee_config(self):
        assert _js_fee([ticket(15, 2)], Decimal("0"), Decimal("0")) == Decimal("0")

    def test_rounding_before_min_check(self):
        # €21: round(21×2.9%=0.609) = 0.61 → above min → fee 0.61
        assert _js_fee([ticket(21, 1)], FEE_PERCENT, FEE_MIN) == Decimal("0.61")

    def test_accumulation_rounding_two_rows(self):
        # 1×€25 + 1×€25: round(0+0.73) = 0.73; round(0.73+0.73) = 1.46
        assert _js_fee([ticket(25, 1), ticket(25, 1)], FEE_PERCENT, FEE_MIN) == Decimal("1.46")


class TestLiveCartFeeWithReferralDiscount:
    """Percent voucher active — fee uses original (pre-discount) price."""

    def test_discounted_price_below_min_original_above_min(self):
        # Original €25, 30% off → discounted €17.50
        # Fee on original: round(25×2.9%=0.725) = 0.73 → above min
        fee = _js_fee([ticket("17.50", 1, original="25.00")], FEE_PERCENT, FEE_MIN)
        assert fee == Decimal("0.73")

    def test_discounted_price_still_below_min_with_original(self):
        # Original €15, 10% off → discounted €13.50
        # Fee on original: round(15×2.9%=0.435) = 0.44 → min 0.60 applies
        fee = _js_fee([ticket("13.50", 1, original="15.00")], FEE_PERCENT, FEE_MIN)
        assert fee == Decimal("0.60")

    def test_two_tickets_discounted_uses_original(self):
        # Original €25, 30% off → discounted €17.50, qty 2
        # ticket_fee = round(25×2.9%) = 0.73; fee = round(0 + 0.73×2) = 1.46
        fee = _js_fee([ticket("17.50", 2, original="25.00")], FEE_PERCENT, FEE_MIN)
        assert fee == Decimal("1.46")

    def test_no_discount_falls_back_to_discounted_price(self):
        # When original == discounted (no <del> in DOM), behaviour unchanged
        fee = _js_fee([ticket("25.00", 1, original="25.00")], FEE_PERCENT, FEE_MIN)
        assert fee == Decimal("0.73")
