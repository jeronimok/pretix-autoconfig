from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from pretix.base.models import Event, Organizer


@pytest.fixture
def organizer(db):
    return Organizer.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def event(organizer):
    return Event.objects.create(
        organizer=organizer,
        name="Test Event",
        slug="test-event",
        date_from="2026-06-01T18:00:00Z",
        plugins="pretix_autoconfig",
    )


@pytest.mark.django_db
class TestEmailSettings:
    def test_suppress_order_placed_flag_set(self, event):
        assert event.settings.get("autoconfig_suppress_order_placed", as_type=bool) is True

    def test_placed_paid_sales_channels_left_at_default(self, event):
        # Must stay non-empty so the paid email still fires.
        assert event.settings.get("mail_sales_channel_placed_paid") != []

    def test_wrapper_is_installed(self):
        from pretix.base.services import orders as pretix_orders

        assert getattr(pretix_orders, "_autoconfig_suppression_installed", False) is True

    def test_wrapper_short_circuits_when_flag_true(self, event):
        from pretix.base.services import orders as pretix_orders

        # Flag was set to True on event creation by the plugin signal.
        order = MagicMock()
        with patch("pretix.base.services.orders.get_email_context") as ctx:
            pretix_orders._order_placed_email(event, order, None, None, "log", None, [])
        ctx.assert_not_called()
        order.send_mail.assert_not_called()

    def test_wrapper_calls_through_when_flag_false(self, event):
        from pretix.base.services import orders as pretix_orders

        event.settings.set("autoconfig_suppress_order_placed", False)
        order = MagicMock()
        with patch("pretix.base.services.orders.get_email_context", return_value={}):
            pretix_orders._order_placed_email(event, order, None, None, "log", None, [])
        order.send_mail.assert_called_once()


@pytest.fixture
def vat_event(organizer, monkeypatch):
    monkeypatch.setenv("PRETIX_AUTOCONFIG_VAT_RATES", "VAT (9%):9.00,VAT (21%):21.00")
    return Event.objects.create(
        organizer=organizer,
        name="VAT Event",
        slug="vat-event",
        date_from="2026-06-01T18:00:00Z",
        plugins="pretix_autoconfig",
    )


@pytest.mark.django_db
class TestTaxRules:
    def test_no_rules_created_when_env_unset(self, event):
        assert event.tax_rules.count() == 0

    def test_low_vat_rule_created(self, vat_event):
        rule = vat_event.tax_rules.get(internal_name="vat_9")
        assert rule.rate == Decimal("9.00")
        assert rule.price_includes_tax is True
        assert rule.default is False

    def test_high_vat_rule_created(self, vat_event):
        rule = vat_event.tax_rules.get(internal_name="vat_21")
        assert rule.rate == Decimal("21.00")
        assert rule.price_includes_tax is True
        assert rule.default is False

    def test_both_rules_present(self, vat_event):
        names = set(vat_event.tax_rules.values_list("internal_name", flat=True))
        assert {"vat_9", "vat_21"}.issubset(names)

    def test_idempotent_on_save(self, vat_event):
        before = vat_event.tax_rules.count()
        vat_event.save()
        assert vat_event.tax_rules.count() == before


@pytest.mark.django_db
class TestTicketDownloadSettings:
    def test_ticket_download_enabled(self, event):
        assert event.settings.get("ticket_download") is True

    def test_ticket_download_nonadm_disabled(self, event):
        assert event.settings.get("ticket_download_nonadm") is False


@pytest.mark.django_db
class TestPresaleEndBehavior:
    """Closes the webshop once an event's presale window is over, without ever
    touching `live` (spec: 2026-09-19-event-ended-status-and-webshop-close)."""

    def test_show_items_outside_presale_period_disabled(self, event):
        assert event.settings.get("show_items_outside_presale_period", as_type=bool) is False

    def test_presale_has_ended_text_set(self, event):
        assert event.settings.get("presale_has_ended_text") == "This event has ended. Tickets are no longer available."

    def test_live_flag_untouched(self, event):
        # Never flip live automatically -- door-scanner/check-in access and
        # anything else gated on it must keep working after the event ends.
        assert event.live is False


@pytest.mark.django_db
class TestAttendeeDataSettings:
    def test_attendee_names_asked_off(self, event):
        assert event.settings.get("attendee_names_asked") is False

    def test_attendee_names_required_off(self, event):
        assert event.settings.get("attendee_names_required") is False


@pytest.fixture
def stripe_env(monkeypatch):
    monkeypatch.setenv("PRETIX_AUTOCONFIG_STRIPE_PUBLISHABLE_KEY", "pk_test_123")
    monkeypatch.setenv("PRETIX_AUTOCONFIG_STRIPE_SECRET_KEY", "sk_test_456")


@pytest.fixture
def stripe_event(organizer, stripe_env):
    return Event.objects.create(
        organizer=organizer,
        name="Stripe Event",
        slug="stripe-event",
        date_from="2026-06-01T18:00:00Z",
        plugins="pretix_autoconfig",
    )


@pytest.mark.django_db
class TestStripeConfig:
    def test_stripe_disabled_without_credentials(self, event):
        # The `event` fixture uses no stripe env vars; provider stays off.
        assert event.settings.get("payment_stripe__enabled") is not True

    def test_stripe_enabled_with_credentials(self, stripe_event):
        assert stripe_event.settings.get("payment_stripe__enabled") is True

    def test_publishable_and_secret_keys_set(self, stripe_event):
        assert stripe_event.settings.get("payment_stripe_publishable_key") == "pk_test_123"
        assert stripe_event.settings.get("payment_stripe_secret_key") == "sk_test_456"

    def test_endpoint_set_to_test(self, stripe_event):
        assert stripe_event.settings.get("payment_stripe_endpoint") == "test"

    def test_methods_enabled_from_env(self, organizer, monkeypatch):
        monkeypatch.setenv("PRETIX_AUTOCONFIG_STRIPE_PUBLISHABLE_KEY", "pk_test_x")
        monkeypatch.setenv("PRETIX_AUTOCONFIG_STRIPE_SECRET_KEY", "sk_test_x")
        monkeypatch.setenv("PRETIX_AUTOCONFIG_STRIPE_METHODS", "card,ideal,walletdetection")
        ev = Event.objects.create(
            organizer=organizer,
            name="Stripe Methods Event",
            slug="stripe-methods",
            date_from="2026-06-01T18:00:00Z",
            plugins="pretix_autoconfig",
        )
        assert ev.settings.get("payment_stripe_method_card") is True
        assert ev.settings.get("payment_stripe_method_ideal") is True
        assert ev.settings.get("payment_stripe_walletdetection") is True

    def test_disabled_providers_turned_off(self, organizer, monkeypatch):
        monkeypatch.setenv("PRETIX_AUTOCONFIG_STRIPE_PUBLISHABLE_KEY", "pk_test_x")
        monkeypatch.setenv("PRETIX_AUTOCONFIG_STRIPE_SECRET_KEY", "sk_test_x")
        monkeypatch.setenv("PRETIX_AUTOCONFIG_DISABLED_PROVIDERS", "giftcard,banktransfer")
        ev = Event.objects.create(
            organizer=organizer,
            name="Disabled Providers Event",
            slug="disabled-providers",
            date_from="2026-06-01T18:00:00Z",
            plugins="pretix_autoconfig",
        )
        assert ev.settings.get("payment_giftcard__enabled") is False
        assert ev.settings.get("payment_banktransfer__enabled") is False


class TestEnvAndConfigHelpers:
    def test_get_stripe_methods_from_env(self, monkeypatch):
        from pretix_autoconfig.signals_config import get_stripe_methods

        monkeypatch.setenv("PRETIX_AUTOCONFIG_STRIPE_METHODS", "card, ideal , bancontact")
        assert get_stripe_methods() == ["card", "ideal", "bancontact"]

    def test_get_stripe_methods_returns_none_when_unset(self, monkeypatch):
        from pretix_autoconfig.signals_config import get_stripe_methods

        monkeypatch.delenv("PRETIX_AUTOCONFIG_STRIPE_METHODS", raising=False)
        with patch("pretix.settings.config.get", return_value=None):
            assert get_stripe_methods() is None

    def test_get_disabled_providers_from_env(self, monkeypatch):
        from pretix_autoconfig.signals_config import get_disabled_providers

        monkeypatch.setenv("PRETIX_AUTOCONFIG_DISABLED_PROVIDERS", "giftcard, banktransfer")
        assert get_disabled_providers() == ["giftcard", "banktransfer"]

    def test_get_primary_color_default(self, monkeypatch):
        from pretix_autoconfig.signals_config import get_primary_color

        monkeypatch.delenv("PRETIX_AUTOCONFIG_PRIMARY_COLOR", raising=False)
        with patch("pretix.settings.config.get", return_value=None):
            assert get_primary_color() is None

    def test_get_primary_color_from_env(self, monkeypatch):
        from pretix_autoconfig.signals_config import get_primary_color

        monkeypatch.setenv("PRETIX_AUTOCONFIG_PRIMARY_COLOR", "#123456")
        assert get_primary_color() == "#123456"

    def test_get_terms_url_unset(self, monkeypatch):
        from pretix_autoconfig.signals_config import get_terms_url

        monkeypatch.delenv("PRETIX_AUTOCONFIG_TERMS_URL", raising=False)
        with patch("pretix.settings.config.get", return_value=None):
            assert get_terms_url() is None

    def test_get_terms_url_from_env(self, monkeypatch):
        from pretix_autoconfig.signals_config import get_terms_url

        monkeypatch.setenv("PRETIX_AUTOCONFIG_TERMS_URL", "https://example.com/terms/")
        assert get_terms_url() == "https://example.com/terms/"

    def test_get_vat_rates_unset(self, monkeypatch):
        from pretix_autoconfig.signals_config import get_vat_rates

        monkeypatch.delenv("PRETIX_AUTOCONFIG_VAT_RATES", raising=False)
        with patch("pretix.settings.config.get", return_value=None):
            assert get_vat_rates() == []

    def test_get_vat_rates_from_env(self, monkeypatch):
        from pretix_autoconfig.signals_config import get_vat_rates

        monkeypatch.setenv("PRETIX_AUTOCONFIG_VAT_RATES", "VAT (9%):9.00,VAT (21%):21.00")
        rates = get_vat_rates()
        assert rates == [("VAT (9%)", Decimal("9.00")), ("VAT (21%)", Decimal("21.00"))]

    def test_no_terms_checkbox_when_url_unset(self, monkeypatch):
        from pretix_autoconfig.signals_config import get_terms_url

        monkeypatch.delenv("PRETIX_AUTOCONFIG_TERMS_URL", raising=False)
        with patch("pretix.settings.config.get", return_value=None):
            assert get_terms_url() is None


@pytest.mark.django_db
class TestTermsConfirmText:
    def test_no_checkbox_when_terms_url_unset(self, event):
        assert not event.settings.get("confirm_texts")

    def test_checkbox_added_when_terms_url_set(self, organizer, monkeypatch):
        monkeypatch.setenv("PRETIX_AUTOCONFIG_TERMS_URL", "https://example.com/terms/")
        ev = Event.objects.create(
            organizer=organizer,
            name="Terms Event",
            slug="terms-event",
            date_from="2026-06-01T18:00:00Z",
            plugins="pretix_autoconfig",
        )
        texts = ev.settings.get("confirm_texts")
        assert texts
        assert "https://example.com/terms/" in str(texts[0])


@pytest.mark.django_db
class TestGuestItem:
    # Item queries are organizer-scoped by django-scopes; the plugin code itself opens its
    # own scope (see _configure_guest_item_on_event_created), but reading the result back
    # here needs one too.
    @pytest.fixture(autouse=True)
    def _scopes_disabled(self):
        from django_scopes import scopes_disabled

        with scopes_disabled():
            yield

    def test_guest_item_created(self, event):
        item = event.items.get(internal_name="guest")
        assert str(item.name) == "Guest"
        assert item.default_price == Decimal("0.00")
        assert item.admission is True
        assert item.active is True

    def test_guest_item_not_on_any_sales_channel(self, event):
        item = event.items.get(internal_name="guest")
        assert item.all_sales_channels is False
        assert item.limit_sales_channels.count() == 0

    def test_guest_item_has_no_quota(self, event):
        item = event.items.get(internal_name="guest")
        assert item.quotas.count() == 0

    def test_idempotent_on_save(self, event):
        before = event.items.filter(internal_name="guest").count()
        event.save()
        assert event.items.filter(internal_name="guest").count() == before == 1

    def test_internal_name_configurable(self, organizer, monkeypatch):
        monkeypatch.setenv("PRETIX_AUTOCONFIG_GUEST_ITEM_INTERNAL_NAME", "legacy_guest")
        ev = Event.objects.create(
            organizer=organizer,
            name="Configured",
            slug="configured",
            date_from="2026-06-01T18:00:00Z",
            plugins="pretix_autoconfig",
        )
        assert ev.items.filter(internal_name="legacy_guest").count() == 1
        assert not ev.items.filter(internal_name="guest").exists()
