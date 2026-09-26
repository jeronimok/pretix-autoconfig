"""Guards for the session-scoped cart state endpoint.

`live_cart.js` drives the quantity inputs from this payload. Two properties matter:
the counts must be per position (not per Pretix's *grouped* `cart.positions`, which is
what made a three-ticket cart render as "1"), and the cart must be resolved from the
session alone — never from anything the caller can pass in. See spec
2026-09-20-private-link-voucher-lock.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import RequestFactory

from pretix_autoconfig.views_cart import _position_key, cart_json

SOURCE = (Path(__file__).resolve().parent.parent / "pretix_autoconfig" / "views_cart.py").read_text()


def _request(path="/some-org/some-event/autoconfig/cart.json"):
    request = RequestFactory().get(path)
    request.event = SimpleNamespace(currency="EUR", organizer=SimpleNamespace(slug="some-org"))
    return request


class TestPositionKey:
    def test_item_without_variation(self):
        assert _position_key(SimpleNamespace(item_id=7, variation_id=None)) == "item_7"

    def test_item_with_variation(self):
        assert _position_key(SimpleNamespace(item_id=7, variation_id=3)) == "variation_7_3"


class TestCartJson:
    def test_empty_payload_when_session_has_no_cart(self):
        with patch("pretix.presale.views.cart.get_or_create_cart_id", return_value=None):
            response = cart_json(_request(), organizer="some-org", event="some-event")
        assert response.status_code == 200
        assert response.content == (
            b'{"positions": [], "quantities": {}, "total": "0.00", "currency": "EUR", "expires": null}'
        )

    def test_rejects_non_get(self):
        request = RequestFactory().post("/some-org/some-event/autoconfig/cart.json")
        request.event = SimpleNamespace(currency="EUR", organizer=SimpleNamespace(slug="some-org"))
        assert cart_json(request, organizer="some-org", event="some-event").status_code == 405


class TestCartIsSessionScoped:
    @pytest.mark.parametrize("forbidden", ["request.GET", "request.POST", "cart_id=request"])
    def test_cart_id_never_comes_from_the_caller(self, forbidden):
        # The cart id must only ever come from get_or_create_cart_id(request), which reads
        # the session. Accepting one from the request would expose other buyers' carts.
        assert forbidden not in SOURCE

    def test_cart_id_is_read_from_the_session_helper(self):
        assert "get_or_create_cart_id(request, create=False)" in SOURCE

    def test_never_creates_a_cart(self):
        # create=True would hand out a cart id to a crawler and start a session for it.
        assert "get_or_create_cart_id(request)" not in SOURCE


class TestCartJsonIsNeverCached:
    """A stale cached copy of this endpoint — served from the browser's HTTP cache, or
    replayed on a back/forward navigation — shows the wrong quantity while the real server
    cart differs. A "+" from that stale display then adds on top of what the server
    already holds: the self-inflicted voucher-locked-in-a-cart failure this endpoint exists
    to prevent."""

    def test_empty_cart_response_is_not_cached(self):
        with patch("pretix.presale.views.cart.get_or_create_cart_id", return_value=None):
            response = cart_json(_request(), organizer="some-org", event="some-event")
        assert response["Cache-Control"] == "no-store"

    def test_populated_and_empty_position_responses_are_not_cached(self):
        assert SOURCE.count('response["Cache-Control"] = "no-store"') == 3
