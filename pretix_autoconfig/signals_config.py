import logging
import os
from decimal import Decimal, InvalidOperation

from django.db.models.signals import post_save
from django.dispatch import receiver
from pretix.base.models import Event, Item, Organizer, TaxRule

from pretix_autoconfig.ticket_layout import apply_branded_layout

logger = logging.getLogger(__name__)


def get_primary_color():
    value = os.environ.get("PRETIX_AUTOCONFIG_PRIMARY_COLOR")
    if value:
        return value
    try:
        from pretix.settings import config

        return config.get("pretix_autoconfig", "primary_color", fallback=None) or None
    except Exception:
        return None


def get_stripe_methods():
    value = os.environ.get("PRETIX_AUTOCONFIG_STRIPE_METHODS")
    if value:
        return [m.strip() for m in value.split(",") if m.strip()]
    try:
        from pretix.settings import config

        raw = config.get("pretix_autoconfig", "stripe_methods", fallback=None)
        if raw:
            return [m.strip() for m in raw.split(",") if m.strip()]
    except Exception:
        pass
    return None


def get_disabled_providers():
    value = os.environ.get("PRETIX_AUTOCONFIG_DISABLED_PROVIDERS")
    if value:
        return [p.strip() for p in value.split(",") if p.strip()]
    try:
        from pretix.settings import config

        raw = config.get("pretix_autoconfig", "disabled_providers", fallback=None)
        if raw:
            return [p.strip() for p in raw.split(",") if p.strip()]
    except Exception:
        pass
    return None


def get_stripe_publishable_key():
    value = os.environ.get("PRETIX_AUTOCONFIG_STRIPE_PUBLISHABLE_KEY")
    if value:
        return value
    try:
        from pretix.settings import config

        return config.get("stripe", "publishable_key", fallback=None) or None
    except Exception:
        return None


def get_stripe_secret_key():
    value = os.environ.get("PRETIX_AUTOCONFIG_STRIPE_SECRET_KEY")
    if value:
        return value
    try:
        from pretix.settings import config

        return config.get("stripe", "secret_key", fallback=None) or None
    except Exception:
        return None


def get_platform_url():
    """Public URL of the platform dashboard (used to link T&C from Pretix)."""
    value = os.environ.get("PRETIX_AUTOCONFIG_PLATFORM_URL")
    if value:
        return value.rstrip("/")
    try:
        from pretix.settings import config

        raw = config.get("pretix_autoconfig", "platform_url", fallback=None)
        return raw.rstrip("/") if raw else None
    except Exception:
        return None


def get_terms_url():
    value = os.environ.get("PRETIX_AUTOCONFIG_TERMS_URL")
    if value:
        return value
    try:
        from pretix.settings import config

        return config.get("pretix_autoconfig", "terms_url", fallback=None) or None
    except Exception:
        return None


def get_favicon_url():
    """URL of a favicon to replace pretix's, or None to leave pretix's alone.

    A URL rather than a path: the browser fetches this, so it has to be served
    by something. Unset means the plugin does not touch the favicon links.
    """
    value = os.environ.get("PRETIX_AUTOCONFIG_FAVICON_URL")
    if value:
        return value
    try:
        from pretix.settings import config

        return config.get("pretix_autoconfig", "favicon_url", fallback=None) or None
    except Exception:
        return None


def get_hidden_question_prefixes():
    """Identifier prefixes whose questions the cart summary should not repeat.

    Questions the platform manages itself are rendered by its own UI, so showing
    them again in pretix's cart is duplication. ``autoconfig_`` is always hidden
    because this plugin creates those; a platform that prefixes its own questions
    differently adds them here.
    """
    value = os.environ.get("PRETIX_AUTOCONFIG_HIDDEN_QUESTION_PREFIXES")
    if not value:
        try:
            from pretix.settings import config

            value = config.get("pretix_autoconfig", "hidden_question_prefixes", fallback=None)
        except Exception:
            value = None
    prefixes = [p.strip() for p in (value or "").split(",") if p.strip()]
    if "autoconfig_" not in prefixes:
        prefixes.append("autoconfig_")
    return prefixes


def get_ticket_background_path():
    """Filesystem path of the PDF used as the ticket background, or None.

    Unset means the plugin leaves ticket layouts alone. The file only has to be
    readable when a layout is applied -- its bytes are copied onto the layout --
    so it lives outside the package rather than on the static path.
    """
    value = os.environ.get("PRETIX_AUTOCONFIG_TICKET_BACKGROUND")
    if value:
        return value
    try:
        from pretix.settings import config

        return config.get("pretix_autoconfig", "ticket_background", fallback=None) or None
    except Exception:
        return None


def get_strip_pretix_logo():
    """Whether to drop pretix's "poweredby" element from the ticket layout.

    Off by default and deliberately independent of the background: removing
    upstream attribution is the installer's decision to make, not this plugin's
    to make on their behalf. pretix's LICENSE requires the notice on generated
    web pages; a downloaded PDF is not one, so removing it is permitted for
    those who choose to.
    """
    value = os.environ.get("PRETIX_AUTOCONFIG_STRIP_PRETIX_LOGO")
    if value is None:
        try:
            from pretix.settings import config

            value = config.get("pretix_autoconfig", "strip_pretix_logo", fallback=None)
        except Exception:
            value = None
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def get_guest_item_internal_name():
    """Internal name of the auto-created guest item.

    A stable admin-only identifier: the platform finds the item to issue guest tickets
    against by this, not by the (organizer-renameable) display name. Configurable so an
    installation that already has guest items under another name keeps finding them.
    """
    value = os.environ.get("PRETIX_AUTOCONFIG_GUEST_ITEM_INTERNAL_NAME")
    if value:
        return value
    try:
        from pretix.settings import config

        return config.get("pretix_autoconfig", "guest_item_internal_name", fallback=None) or "guest"
    except Exception:
        return "guest"


def get_terms_name():
    value = os.environ.get("PRETIX_AUTOCONFIG_TERMS_NAME")
    if value:
        return value
    try:
        from pretix.settings import config

        return config.get("pretix_autoconfig", "terms_name", fallback=None) or "terms and conditions"
    except Exception:
        return "terms and conditions"


def get_vat_rates():
    """Return list of (name, rate) tuples from config, or empty list if not configured."""
    value = os.environ.get("PRETIX_AUTOCONFIG_VAT_RATES")
    if not value:
        try:
            from pretix.settings import config

            value = config.get("pretix_autoconfig", "vat_rates", fallback=None)
        except Exception:
            pass
    if not value:
        return []
    result = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        if ":" not in chunk:
            continue
        name, _, rate_str = chunk.partition(":")
        try:
            rate = Decimal(rate_str.strip())
        except InvalidOperation:
            continue
        result.append((name.strip(), rate))
    return result


def _configure_stripe_on_event_created(event):
    publishable_key = get_stripe_publishable_key()
    secret_key = get_stripe_secret_key()
    if not publishable_key or not secret_key:
        return

    event.settings.set("payment_stripe__enabled", True)

    disabled_providers = get_disabled_providers()
    if disabled_providers:
        for provider in disabled_providers:
            event.settings.set(f"payment_{provider}__enabled", False)

    event.settings.set("payment_stripe_publishable_key", publishable_key)
    event.settings.set("payment_stripe_secret_key", secret_key)
    event.settings.set("payment_stripe_endpoint", "live" if publishable_key.startswith("pk_live_") else "test")

    methods = get_stripe_methods()
    if methods:
        for method in methods:
            if method == "walletdetection":
                event.settings.set("payment_stripe_walletdetection", True)
            else:
                event.settings.set(f"payment_stripe_method_{method}", True)


def _configure_ticket_download_settings(event):
    event.settings.set("ticket_download", True)
    event.settings.set("ticket_download_nonadm", False)


_PRESALE_HAS_ENDED_TEXT = "This event has ended. Tickets are no longer available."


def _configure_presale_end_behavior(event):
    """Stop the shop looking sellable once the event's presale window is over.

    pretix's own `presale_has_ended` already blocks checkout past `presale_end`
    (falling back to `date_to`, then `date_from`), but by default it leaves the
    full item list and "add to cart" form visible above a small inline banner --
    a buyer landing there sees what still looks like a live shop. Turning off
    `show_items_outside_presale_period` hides that item list once presale ends
    (it also hides it before a configured `presale_start`, which is fine: this
    platform's event-creation flow doesn't set one). This never touches the
    event's `live` flag, so it can't affect anything gated on that instead
    (e.g. door-scanner/check-in device access).
    """
    event.settings.set("show_items_outside_presale_period", False)
    event.settings.set("presale_has_ended_text", _PRESALE_HAS_ENDED_TEXT)


def _configure_ticket_layout(event):
    """Give the event the configured PDF ticket branding.

    Runs inside event creation, so a failure here must not take the whole
    event down with it -- an unbranded ticket is recoverable, a half-created
    event is not. The backfill command picks up anything that fails.
    """
    try:
        apply_branded_layout(event)
    except Exception:
        logger.exception(
            "Could not apply the branded ticket layout to %s/%s",
            event.organizer.slug,
            event.slug,
        )


_EMAIL_SUBJECT = "Your ticket(s) for {event}"

_EMAIL_BODY = """\
<p class="order-button">Hello,</p>

<p class="order-button">your ticket(s) for {event} are confirmed.</p>

<p class="order-button"><a href="{url}" class="button">Download tickets</a></p>

<p class="order-button"><a href="{url}">View order details</a></p>\
"""


def _configure_email_settings(event):
    # Suppress the "order placed" email so customers receive only the payment
    # confirmation email (which includes the ticket). Pretix's built-in
    # `mail_sales_channel_placed_paid` setting gates BOTH placed and paid
    # emails, so we use a plugin-specific flag honored by the monkey-patch
    # installed in apps.py.
    event.settings.set("autoconfig_suppress_order_placed", True)
    event.settings.set("mail_subject_order_paid", _EMAIL_SUBJECT)
    event.settings.set("mail_subject_order_free", _EMAIL_SUBJECT)
    event.settings.set("mail_text_order_paid", _EMAIL_BODY)
    event.settings.set("mail_text_order_free", _EMAIL_BODY)


def _configure_terms_confirm_text(event):
    """Add a mandatory T&C checkbox on the checkout confirm step.

    Only runs when PRETIX_AUTOCONFIG_TERMS_URL is configured. No-op otherwise.
    Does not overwrite an organizer's existing custom confirm_texts.
    """
    terms_url = get_terms_url()
    if not terms_url:
        return
    if event.settings.get("confirm_texts"):
        return
    terms_name = get_terms_name()
    link = f'<a href="{terms_url}" target="_blank" rel="noopener">{terms_name}</a>'
    event.settings.set(
        "confirm_texts",
        [{"en": f"I accept the {link}"}],
    )


def _configure_attendee_data_off(event):
    # Customer-info collection on this platform uses per-order Questions, not
    # Pretix's per-attendee built-ins. Pretix defaults attendee_names_asked
    # to True, which surfaces "Given/Family name" on checkout in addition
    # to our own First/Last name Questions. Force these off so the spec
    # promise "default = only email asked" holds.
    event.settings.set("attendee_names_asked", False)
    event.settings.set("attendee_names_required", False)


def _configure_tax_rules_on_event_created(event):
    """Create VAT tax rules if PRETIX_AUTOCONFIG_VAT_RATES is configured. No-op otherwise."""
    rates = get_vat_rates()
    if not rates:
        return
    for name, rate in rates:
        internal_name = f"vat_{str(rate).replace('.', '_').rstrip('0').rstrip('_')}"
        if event.tax_rules.filter(internal_name=internal_name).exists():
            continue
        TaxRule.objects.create(
            event=event,
            name={"en": name},
            internal_name=internal_name,
            rate=rate,
            price_includes_tax=True,
            default=False,
        )


def _configure_guest_item_on_event_created(event):
    """Create a hidden, free "Guest" item so guest tickets stop sharing an item with paid ones.

    Not visible in the webshop: the storefront's product listing only shows items with
    ``all_sales_channels=True`` or a ``web`` channel in ``limit_sales_channels``, so setting
    ``all_sales_channels=False`` with no channels hides it there. Backend order creation (how
    the platform issues guest tickets) only checks ``item.active``, not sales channel, so this item
    stays orderable through that path. No quota is assigned -- it's never sold, so nothing to cap.
    """
    from django_scopes import scope

    with scope(organizer=event.organizer):
        internal_name = get_guest_item_internal_name()
        if event.items.filter(internal_name=internal_name).exists():
            return
        Item.objects.create(
            event=event,
            name={"en": "Guest"},
            internal_name=internal_name,
            default_price=Decimal("0.00"),
            admission=True,
            personalized=True,
            all_sales_channels=False,
        )


@receiver(post_save, sender=Organizer)
def configure_organizer_on_save(sender, instance, created, **kwargs):
    if created:
        primary_color = get_primary_color()
        if primary_color:
            instance.settings.set("primary_color", primary_color)
        instance.settings.set("autoconfig_branding_canvas_bg", "#ececec")
        instance.settings.set("autoconfig_branding_canvas_text", "#111111")
        instance.settings.set("autoconfig_branding_card_bg", "#ffffff")
        instance.settings.set("autoconfig_branding_card_text", "#111111")
        instance.settings.set("autoconfig_branding_dark_mode", False)


@receiver(post_save, sender=Event)
def configure_event_on_save(sender, instance, created, **kwargs):
    if created:
        _configure_tax_rules_on_event_created(instance)
        _configure_ticket_download_settings(instance)
        _configure_presale_end_behavior(instance)
        _configure_ticket_layout(instance)
        _configure_terms_confirm_text(instance)
        _configure_attendee_data_off(instance)
        _configure_email_settings(instance)
        _configure_stripe_on_event_created(instance)
        _configure_guest_item_on_event_created(instance)
