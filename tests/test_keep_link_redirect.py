"""KeepLinkOnShopRedirect: checkout bouncing back to the shop keeps the private link.

Spec 2026-09-23-private-link-combined-cap.
"""

from datetime import timedelta

import pytest
from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory
from django.utils.timezone import now
from pretix.base.models import Event, Organizer
from pretix.multidomain.urlreverse import eventreverse

from pretix_autoconfig.middleware import KeepLinkOnShopRedirect
from pretix_autoconfig.signals_referral import capture_referral_on_page_load


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


def _run(event, path, response, arrived_with="?voucher=PRIV-LINK&ref=abc123"):
    arrival = RequestFactory().get(f"/org/e/{arrived_with}")
    arrival.session = {}
    capture_referral_on_page_load(event, arrival)
    request = RequestFactory().get(path)
    request.session = arrival.session
    request.event = event
    return KeepLinkOnShopRedirect(lambda r: response)(request)


@pytest.mark.django_db
class TestKeepLinkOnShopRedirect:
    def test_checkout_bounce_to_the_shop_keeps_the_link(self, event):
        index = eventreverse(event, "presale:event.index")
        response = _run(event, "/org/e/checkout/questions/", HttpResponseRedirect(index))
        assert response["Location"] == f"{index}?voucher=PRIV-LINK&ref=abc123"

    def test_pretix_own_parameters_are_kept(self, event):
        # Pretix's checkout bounce is `?require_cookie=true`: the link goes in next to it.
        index = eventreverse(event, "presale:event.index")
        response = _run(event, "/org/e/checkout/start", HttpResponseRedirect(f"{index}?require_cookie=true"))
        assert response["Location"] == f"{index}?voucher=PRIV-LINK&ref=abc123&require_cookie=true"

    def test_a_redirect_that_already_has_a_voucher_is_left_alone(self, event):
        index = eventreverse(event, "presale:event.index")
        response = _run(event, "/org/e/checkout/questions/", HttpResponseRedirect(f"{index}?voucher=OTHER"))
        assert response["Location"] == f"{index}?voucher=OTHER"

    def test_redirect_to_another_checkout_step_is_left_alone(self, event):
        target = eventreverse(event, "presale:event.checkout", kwargs={"step": "questions"})
        response = _run(event, "/org/e/checkout/confirm/", HttpResponseRedirect(target))
        assert response["Location"] == target

    def test_non_checkout_pages_are_left_alone(self, event):
        index = eventreverse(event, "presale:event.index")
        response = _run(event, "/org/e/redeem", HttpResponseRedirect(index))
        assert response["Location"] == index

    def test_buyer_without_a_link_gets_the_plain_shop(self, event):
        index = eventreverse(event, "presale:event.index")
        response = _run(event, "/org/e/checkout/questions/", HttpResponseRedirect(index), arrived_with="")
        assert response["Location"] == index

    def test_non_redirects_pass_through(self, event):
        page = HttpResponse("ok")
        assert _run(event, "/org/e/checkout/questions/", page) is page
