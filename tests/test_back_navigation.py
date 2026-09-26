"""Private-link buyers going back to the shop, and re-entering a discount code.

Spec 2026-09-23-private-link-back-navigation. Covers the template tags behind the checkout
"‹ Back" link and the Review page's voucher card, and the link counts `cart_json` hands to
`live_cart.js` so a refused change is worded truthfully.
"""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.test import RequestFactory
from django.utils.timezone import now
from pretix.base.models import CartPosition, Event, Item, Organizer, Voucher

from pretix_autoconfig.signals_referral import capture_referral_on_page_load
from pretix_autoconfig.templatetags.autoconfig_fees import cart_discount_state, shop_return_url
from pretix_autoconfig.views_cart import _link_state


@pytest.fixture(autouse=True)
def _scopes_disabled(db):
    from django_scopes import scopes_disabled

    with scopes_disabled():
        yield


@pytest.fixture
def event(db):
    organizer = Organizer.objects.create(name="Org", slug="org")
    return Event.objects.create(
        organizer=organizer, name="E", slug="e", date_from=now() + timedelta(days=30), plugins="pretix_autoconfig"
    )


@pytest.fixture
def priv_voucher(event):
    return Voucher.objects.create(
        event=event, code="PRIV-LINK", tag="private:7", price_mode="none", allow_ignore_quota=True, max_usages=3
    )


def _request(event, query=""):
    request = RequestFactory().get(f"/org/e/{query}")
    request.session = {}
    request.event = event
    capture_referral_on_page_load(event, request)
    return request


# ---------------------------------------------------------------------------
# shop_return_url
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShopReturnUrl:
    def test_keeps_the_voucher_and_ref_the_buyer_arrived_with(self, event):
        request = _request(event, "?voucher=PRIV-LINK&ref=abc123")
        url = shop_return_url({"request": request, "event": event})
        assert url.endswith("/org/e/?voucher=PRIV-LINK&ref=abc123")

    def test_a_later_page_without_the_voucher_does_not_drop_it(self, event):
        request = _request(event, "?voucher=PRIV-LINK&ref=abc123")
        request.GET = RequestFactory().get("/org/e/checkout/questions/").GET
        capture_referral_on_page_load(event, request)
        assert shop_return_url({"request": request, "event": event}).endswith("?voucher=PRIV-LINK&ref=abc123")

    def test_plain_shop_url_when_the_buyer_came_without_a_link(self, event):
        url = shop_return_url({"request": _request(event), "event": event})
        assert url.endswith("/org/e/")


# ---------------------------------------------------------------------------
# cart_discount_state
# ---------------------------------------------------------------------------


def _line(voucher, count=1):
    return SimpleNamespace(voucher=voucher, count=count)


class TestCartDiscountState:
    link = SimpleNamespace(price_mode="none")
    code = SimpleNamespace(price_mode="percent", value=Decimal("10"))

    def test_all_tickets_discounted(self):
        state = cart_discount_state({"positions": [_line(self.code, 2)]})
        assert (state["applied"], state["total"], state["voucher"]) == (2, 2, self.code)

    def test_tickets_added_after_the_code(self):
        state = cart_discount_state({"positions": [_line(self.code, 2), _line(self.link, 1)]})
        assert (state["applied"], state["total"], state["voucher"]) == (2, 3, self.code)

    def test_bypass_only_cart_is_not_a_discount(self):
        state = cart_discount_state({"positions": [_line(self.link, 3)]})
        assert (state["applied"], state["total"], state["voucher"]) == (0, 3, None)

    def test_empty_cart(self):
        assert cart_discount_state({"positions": []}) == {"voucher": None, "applied": 0, "total": 0}


# ---------------------------------------------------------------------------
# _link_state (cart_json "link" block)
# ---------------------------------------------------------------------------


def _hold(event, voucher, cart_id, expires_in=10):
    item = event.items.first() or Item.objects.create(event=event, name="GA", default_price=Decimal("20.00"))
    return CartPosition.objects.create(
        event=event,
        cart_id=cart_id,
        item=item,
        voucher=voucher,
        price=Decimal("20.00"),
        listed_price=Decimal("20.00"),
        price_after_voucher=Decimal("20.00"),
        expires=now() + timedelta(minutes=expires_in),
        max_extend=now() + timedelta(minutes=30),
    )


@pytest.mark.django_db
class TestLinkState:
    def test_none_without_a_private_link(self, event):
        assert _link_state(_request(event), event, "mine") is None

    def test_own_cart_is_not_someone_else(self, event, priv_voucher):
        _hold(event, priv_voucher, "mine")
        _hold(event, priv_voucher, "mine")
        state = _link_state(_request(event, "?voucher=PRIV-LINK"), event, "mine")
        assert state == {"cap": 3, "redeemed": 0, "held_by_others": 0}

    def test_other_live_carts_count_including_combined_lines(self, event, priv_voucher):
        combo = Voucher.objects.create(
            event=event, code="CMB-X", tag="private:7:combo:SAVE10", price_mode="percent", value=Decimal("10")
        )
        _hold(event, priv_voucher, "other")
        _hold(event, combo, "third")
        _hold(event, priv_voucher, "stale", expires_in=-1)
        state = _link_state(_request(event, "?voucher=PRIV-LINK"), event, "mine")
        assert state["held_by_others"] == 2

    def test_another_link_is_not_counted(self, event, priv_voucher):
        other_link = Voucher.objects.create(
            event=event, code="PRIV-OTHER", tag="private:70", price_mode="none", allow_ignore_quota=True
        )
        _hold(event, other_link, "other")
        assert _link_state(_request(event, "?voucher=PRIV-LINK"), event, "mine")["held_by_others"] == 0
