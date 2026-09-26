"""Regression guards for the checkout voucher card.

A private link is backed by a Pretix voucher; a sold-out-bypass link uses
``price_mode="none"`` (no price change). Such a voucher must NOT render as
"discount applied", and it must NOT hide the promo-code input — the buyer may
still have a real discount code to enter.

Rendering the full pretix checkout templates needs the whole presale context
chain, so these tests assert on template source. The end-to-end behaviour is
covered by manual runtime verification (spec 2026-09-07).
"""

from pathlib import Path

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "pretix_autoconfig" / "templates" / "pretixpresale" / "event"


@pytest.fixture
def confirm_src():
    return (TEMPLATE_DIR / "checkout_confirm.html").read_text()


@pytest.fixture
def cart_box_src():
    return (TEMPLATE_DIR / "fragment_cart_box.html").read_text()


class TestCheckoutConfirmVoucherCard:
    def test_discount_applied_message_is_gated_on_real_price_change(self, confirm_src):
        # The "discount applied" branch must require every ticket to carry a voucher that
        # actually changes the price (cart_discount_state counts price_mode != "none"),
        # not merely that every line carries a voucher.
        assert "{% cart_discount_state cart as ds %}" in confirm_src
        assert "{% if ds.applied and ds.applied == ds.total %}" in confirm_src

    def test_promo_input_present_for_non_discount_voucher(self, confirm_src):
        # The {% else %} of that guard still renders the promo <form>.
        guard_idx = confirm_src.index("{% if ds.applied and ds.applied == ds.total %}")
        tail = confirm_src[guard_idx:]
        assert "{% else %}" in tail
        assert 'name="voucher"' in tail
        assert "presale:event.cart.voucher" in tail

    def test_bypass_code_is_not_prefilled_into_promo_box(self, confirm_src):
        # Only pre-fill when the session voucher is an actual discount (ref_pct != "0").
        assert "{% if ref_pct != '0' %}{{ ref_voucher }}{% endif %}" in confirm_src

    def test_uses_comment_tag_not_bare_multiline_hash(self, confirm_src):
        # Django {# #} is single-line only; a multi-line one leaks to the page.
        # Guard against reintroducing that bug in this file.
        for line in confirm_src.splitlines():
            stripped = line.strip()
            if stripped.startswith("{#"):
                assert stripped.endswith("#}"), f"multi-line {{# #}} comment leaks: {line!r}"


class TestFragmentCartBoxVoucherInput:
    def test_voucher_input_survives_a_non_discount_voucher(self, cart_box_src):
        assert "not cart.all_with_voucher or cart_box_voucher.price_mode == 'none'" in cart_box_src

    def test_uses_comment_tag_not_bare_multiline_hash(self, cart_box_src):
        for line in cart_box_src.splitlines():
            stripped = line.strip()
            if stripped.startswith("{#"):
                assert stripped.endswith("#}"), f"multi-line {{# #}} comment leaks: {line!r}"


class TestPartialDiscountOffersTheCodeAgain:
    """Spec 2026-09-23-private-link-back-navigation: tickets added after the code was applied."""

    def test_partial_discount_is_worded_and_keeps_the_promo_form(self, confirm_src):
        partial = confirm_src.index("{% if ds.applied %}")
        tail = confirm_src[partial:]
        assert "discount applied to {{ applied }} of {{ total }} tickets" in tail
        assert tail.index("{% endif %}") < tail.index('name="voucher"')


class TestCheckoutBackLink:
    def test_first_step_back_returns_to_the_arrival_url(self):
        src = (TEMPLATE_DIR / "checkout_base.html").read_text()
        assert '<a class="autoconfig-top-back" href="{% shop_return_url %}">' in src
        assert 'href="{% eventurl request.event "presale:event.index" %}"' not in src
