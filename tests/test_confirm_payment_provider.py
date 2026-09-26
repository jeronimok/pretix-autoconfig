"""The merged confirm step must only accept payment providers Pretix allows for the cart.

Regression: the patched ConfirmStep.post checked only `is_enabled`, which Pretix's implicit
"free" and "boxoffice" providers hard-code to True. POSTing payment=free on a paid cart placed
the order and confirmed it as paid without any money changing hands.
"""

import datetime
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import TestCase
from django.utils.timezone import now
from django_scopes import scopes_disabled
from pretix.base.models import CartPosition, Event, Item, Order, Organizer, Quota
from pretix.testutils.sessions import get_cart_session_key


@pytest.mark.django_db
class ConfirmStepPaymentProviderTest(TestCase):
    def setUp(self):
        with scopes_disabled():
            self.orga = Organizer.objects.create(name="O", slug="o", plugins="pretix.plugins.banktransfer")
            self.event = Event.objects.create(
                organizer=self.orga,
                name="E",
                slug="e",
                date_from=datetime.datetime(now().year + 1, 12, 26, tzinfo=datetime.timezone.utc),
                plugins="pretix_autoconfig,pretix.plugins.banktransfer",
                live=True,
            )
            self.paid = Item.objects.create(event=self.event, name="Ticket", default_price=Decimal("20.00"))
            self.free = Item.objects.create(event=self.event, name="Free", default_price=Decimal("0.00"))
            quota = Quota.objects.create(event=self.event, name="Q", size=10)
            quota.items.add(self.paid, self.free)
            self.event.settings.set("payment_banktransfer__enabled", True)
            self.event.settings.set("attendee_names_asked", False)
        self.client.get("/o/e/")
        self.cart_id = get_cart_session_key(self.client, self.event)
        self._set_session("email", "buyer@example.com")

    def _set_session(self, key, value):
        session = self.client.session
        session["carts"][self.cart_id][key] = value
        session.save()

    def _add(self, item):
        with scopes_disabled():
            CartPosition.objects.create(
                event=self.event,
                cart_id=self.cart_id,
                item=item,
                price=item.default_price,
                listed_price=item.default_price,
                price_after_voucher=item.default_price,
                expires=now() + timedelta(minutes=10),
            )

    def _confirm(self, data):
        response = self.client.post("/o/e/checkout/confirm/", data)
        with scopes_disabled():
            return response, list(Order.objects.filter(event=self.event))

    def _assert_refused(self, response, orders):
        assert orders == []
        assert response.status_code == 302
        assert response["Location"].endswith("/o/e/checkout/confirm/")

    def test_free_provider_refused_on_paid_cart(self):
        self._add(self.paid)
        self._assert_refused(*self._confirm({"payment": "free"}))

    def test_boxoffice_provider_refused_online(self):
        self._add(self.paid)
        self._assert_refused(*self._confirm({"payment": "boxoffice"}))

    def test_unknown_provider_refused(self):
        self._add(self.paid)
        self._assert_refused(*self._confirm({"payment": "nonexistent"}))

    def test_stale_free_selection_refused_after_cart_became_paid(self):
        self._add(self.paid)
        self._set_session(
            "payments",
            [
                {
                    "id": "stale",
                    "provider": "free",
                    "multi_use_supported": False,
                    "min_value": None,
                    "max_value": None,
                    "info_data": {},
                }
            ],
        )
        self._assert_refused(*self._confirm({}))
        assert self.client.session["carts"][self.cart_id]["payments"] == []

    def test_free_provider_still_works_on_free_cart(self):
        self._add(self.free)
        _, orders = self._confirm({"payment": "free"})
        assert len(orders) == 1
        assert orders[0].status == Order.STATUS_PAID
        assert orders[0].total == Decimal("0.00")

    def test_enabled_real_provider_still_works_on_paid_cart(self):
        self._add(self.paid)
        _, orders = self._confirm({"payment": "banktransfer"})
        assert len(orders) == 1
        assert orders[0].status == Order.STATUS_PENDING
        assert orders[0].total == Decimal("20.00")
