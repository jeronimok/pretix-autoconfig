from django.utils.translation import gettext_lazy

from . import __version__

try:
    from pretix.base.plugins import PLUGIN_LEVEL_ORGANIZER, PluginConfig
except ImportError:
    raise RuntimeError("Please use pretix 2.7 or above to run this plugin!")


def _prepend_plugin_templates():
    """Prepend our templates dir to DIRS so pretix template overrides are found first."""
    import os

    from django.conf import settings

    plugin_templates = os.path.join(os.path.dirname(__file__), "templates")
    dirs = settings.TEMPLATES[0].setdefault("DIRS", [])
    if plugin_templates not in dirs:
        dirs.insert(0, plugin_templates)


class PluginApp(PluginConfig):
    default = True
    name = "pretix_autoconfig"
    verbose_name = "Pretix AutoConfig"

    class PretixPluginMeta:
        name = gettext_lazy("Pretix AutoConfig")
        author = "Jeronimo Calace Montu"
        description = gettext_lazy("Auto-configure fees and payment methods on event creation")
        visible = True
        version = __version__
        category = "FEATURE"
        compatibility = "pretix>=2.7.0"
        level = PLUGIN_LEVEL_ORGANIZER
        settings_links = []
        navigation_links = []

    def ready(self):
        _prepend_plugin_templates()
        _allow_shop_iframe()
        _redirect_bare_root()
        _keep_link_on_shop_redirect()
        _install_order_placed_email_suppression()
        _remove_payment_step_from_flow()
        _patch_confirm_step_context()
        _patch_confirm_step_post()
        _suppress_ajax_cart_messages()
        _patch_apply_voucher_message()
        _register_confirm_step_csp()
        _register_branding_settings()
        _patch_event_index_voucher_bypass()
        from . import signals_combo_voucher

        signals_combo_voucher.patch_cart_apply_voucher()
        signals_combo_voucher.patch_voucher_availability()
        signals_combo_voucher.connect_signals()
        from . import (  # NOQA
            signals_branding,
            signals_checkout,
            signals_config,
            signals_fee,
            signals_marketing,
            signals_referral,
        )


def _allow_shop_iframe():
    from django.conf import settings

    middleware = "pretix_autoconfig.middleware.AllowShopIframe"
    if middleware not in settings.MIDDLEWARE:
        settings.MIDDLEWARE = [middleware] + list(settings.MIDDLEWARE)


def _redirect_bare_root():
    """Registered after _allow_shop_iframe() and prepended the same way, so it ends up in front
    of AllowShopIframe: a redirect at "/" should short-circuit before anything else runs, not
    have Pretix's own view render first only for AllowShopIframe to post-process the result.
    """
    from django.conf import settings

    middleware = "pretix_autoconfig.middleware.RedirectBareRootToMarketing"
    if middleware not in settings.MIDDLEWARE:
        settings.MIDDLEWARE = [middleware] + list(settings.MIDDLEWARE)


def _keep_link_on_shop_redirect():
    from django.conf import settings

    middleware = "pretix_autoconfig.middleware.KeepLinkOnShopRedirect"
    if middleware not in settings.MIDDLEWARE:
        settings.MIDDLEWARE = [middleware] + list(settings.MIDDLEWARE)


def _register_branding_settings():
    """Register the organizer-level branding color settings with Pretix.

    Pretix's organizer settings REST API silently drops keys that are not
    declared in `OrganizerSettingsSerializer.default_fields` (and which lack
    a `serializer_class` entry in `pretix.base.settings.DEFAULTS`). Patch
    both to make our keys round-trip through the API.
    """
    from django.core.validators import RegexValidator
    from pretix.api.serializers.organizer import OrganizerSettingsSerializer
    from pretix.base.settings import DEFAULTS
    from rest_framework import serializers as drf_serializers

    if getattr(_register_branding_settings, "_registered", False):
        return

    hex_validator = RegexValidator(
        regex=r"^#[0-9a-fA-F]{6}$",
        message="Enter a 6-digit hex color, e.g. #ff6b6b.",
    )
    branding_keys = {
        "autoconfig_branding_canvas_bg": "#ececec",
        "autoconfig_branding_canvas_text": "#111111",
        "autoconfig_branding_card_bg": "#ffffff",
        "autoconfig_branding_card_text": "#111111",
    }
    for key, default in branding_keys.items():
        DEFAULTS[key] = {
            "default": default,
            "type": str,
            "serializer_class": drf_serializers.CharField,
            "serializer_kwargs": dict(validators=[hex_validator]),
        }
        if key not in OrganizerSettingsSerializer.default_fields:
            OrganizerSettingsSerializer.default_fields.append(key)

    DEFAULTS["autoconfig_branding_dark_mode"] = {
        "default": "False",
        "type": bool,
        "serializer_class": drf_serializers.BooleanField,
    }
    if "autoconfig_branding_dark_mode" not in OrganizerSettingsSerializer.default_fields:
        OrganizerSettingsSerializer.default_fields.append("autoconfig_branding_dark_mode")

    # Per-event toggle controlling whether the optional marketing opt-in
    # checkbox renders on the checkout confirm step. Off by default —
    # organizer enables it explicitly from the event settings page.
    from pretix.api.serializers.event import EventSettingsSerializer

    DEFAULTS["autoconfig_marketing_optin_on_confirm"] = {
        "default": "False",
        "type": bool,
        "serializer_class": drf_serializers.BooleanField,
    }
    if "autoconfig_marketing_optin_on_confirm" not in EventSettingsSerializer.default_fields:
        EventSettingsSerializer.default_fields.append("autoconfig_marketing_optin_on_confirm")

    _register_branding_settings._registered = True


def _patch_apply_voucher_message():
    from django.utils.translation import gettext_lazy as _lazy
    from pretix.presale.views.cart import CartApplyVoucher

    if getattr(CartApplyVoucher, "_autoconfig_message_patched", False):
        return

    CartApplyVoucher.get_success_message = lambda self, value: _lazy("Voucher applied.")
    CartApplyVoucher._autoconfig_message_patched = True


def _suppress_ajax_cart_messages():
    # Pretix's AsyncAction.success() adds a Django message even for AJAX calls.
    # Those messages accumulate in the session and surface on the next page render
    # (e.g. checkout/questions), where they're confusing. Suppress them for AJAX.
    from pretix.presale.views.cart import CartAdd, CartClear

    if getattr(CartClear, "_autoconfig_messages_suppressed", False):
        return

    orig_clear = CartClear.get_success_message
    orig_add = CartAdd.get_success_message

    def clear_success(self, value):
        if "ajax" in self.request.GET or "ajax" in self.request.POST:
            return None
        return orig_clear(self, value)

    def add_success(self, value):
        if "ajax" in self.request.GET or "ajax" in self.request.POST:
            return None
        return orig_add(self, value)

    CartClear.get_success_message = clear_success
    CartAdd.get_success_message = add_success
    CartClear._autoconfig_messages_suppressed = True


def _install_order_placed_email_suppression():
    # Pretix's `mail_sales_channel_placed_paid` setting gates BOTH the
    # order-placed and order-paid emails, so we can't suppress only the
    # placed email through stock settings. Wrap `_order_placed_email` to
    # no-op when the event opts in via `autoconfig_suppress_order_placed`.
    from pretix.base.services import orders as pretix_orders

    if getattr(pretix_orders, "_autoconfig_suppression_installed", False):
        return

    original = pretix_orders._order_placed_email

    def wrapped(event, order, *args, **kwargs):
        if event.settings.get("autoconfig_suppress_order_placed", as_type=bool, default=False):
            # Allow free-order emails through (guestlist invitations use is_free=True)
            if not kwargs.get("is_free", False):
                return
        return original(event, order, *args, **kwargs)

    pretix_orders._order_placed_email = wrapped
    pretix_orders._autoconfig_suppression_installed = True


def _register_confirm_step_csp():
    """
    Stripe's own CSP signal only adds js.stripe.com to script-src for the payment step.
    Since we merged payment selection onto the confirm step, we need the same CSP there.
    """
    from django.urls import resolve
    from pretix.base.middleware import _merge_csp, _parse_csp, _render_csp
    from pretix.presale.signals import process_response

    if getattr(_register_confirm_step_csp, "_registered", False):
        return

    def _add_stripe_csp_for_confirm(sender, request, response, **kwargs):
        try:
            url = resolve(request.path_info)
        except Exception:
            return response
        if url.url_name == "event.checkout" and url.kwargs.get("step") == "confirm":
            if "Content-Security-Policy" in response:
                h = _parse_csp(response["Content-Security-Policy"])
            else:
                h = {}
            _merge_csp(
                h,
                {
                    "script-src": ["https://js.stripe.com", "https://pay.google.com"],
                    "frame-src": ["https://js.stripe.com", "https://hooks.stripe.com", "https://pay.google.com"],
                    "connect-src": ["https://api.stripe.com", "https://google.com/pay"],
                    "img-src": ["https://*.stripe.com"],
                },
            )
            if h:
                response["Content-Security-Policy"] = _render_csp(h)
        return response

    process_response.connect(_add_stripe_csp_for_confirm, dispatch_uid="autoconfig_confirm_stripe_csp", weak=False)
    _register_confirm_step_csp._registered = True


def _remove_payment_step_from_flow():
    import pretix.presale.checkoutflow as _cf

    if getattr(_cf, "_autoconfig_payment_step_removed", False):
        return
    _cf.DEFAULT_FLOW = tuple(s for s in _cf.DEFAULT_FLOW if s.__name__ != "PaymentStep")
    _cf._autoconfig_payment_step_removed = True


def _patch_confirm_step_context():
    import inspect
    from decimal import Decimal

    from pretix.presale.checkoutflow import ConfirmStep

    if getattr(ConfirmStep, "_autoconfig_context_patched", False):
        return

    original_get_context_data = ConfirmStep.get_context_data

    def patched_get_context_data(self, **kwargs):
        ctx = original_get_context_data(self, **kwargs)
        request = self.request
        total = Decimal(str(ctx["cart"]["total"]))

        providers = []
        for provider in sorted(
            request.event.get_payment_providers().values(),
            key=lambda p: (-p.priority, str(p.public_name).title()),
        ):
            if not provider.is_enabled or not provider.is_allowed(request, total=total):
                continue
            fee = provider.calculate_fee(total)
            if "total" in inspect.signature(provider.payment_form_render).parameters:
                form = provider.payment_form_render(request, total + fee)
            else:
                form = provider.payment_form_render(request)
            providers.append({"provider": provider, "fee": fee, "total": total + fee, "form": form})

        ctx["providers"] = providers
        ctx["show_fees"] = any(p["fee"] for p in providers)

        if len(providers) == 1:
            ctx["selected"] = providers[0]["provider"].identifier
        elif "payment" in request.POST:
            ctx["selected"] = request.POST["payment"]
        else:
            singleton = [p for p in self.cart_session.get("payments", []) if not p.get("multi_use_supported")]
            ctx["selected"] = singleton[0]["provider"] if singleton else ""

        return ctx

    ConfirmStep.get_context_data = patched_get_context_data
    ConfirmStep._autoconfig_context_patched = True


def _patch_confirm_step_post():
    from pretix.presale.checkoutflow import ConfirmStep

    if getattr(ConfirmStep, "_autoconfig_post_patched", False):
        return

    from decimal import Decimal

    from django.contrib import messages
    from django.utils.translation import gettext_lazy as _lazy
    from pretix.base.models import TaxRule
    from pretix.base.services.cart import add_payment_to_cart, get_fees
    from pretix.helpers.http import redirect_to_url
    from pretix.presale.views.cart import get_cart

    original_post = ConfirmStep.post

    def _order_total(self):
        # Same computation as Pretix's PaymentStep._total_order_value, which is what its
        # provider checks run against.
        cart = get_cart(self.request)
        try:
            fees = get_fees(
                event=self.request.event,
                request=self.request,
                invoice_address=self.invoice_address,
                payments=[p for p in self.cart_session.get("payments", []) if p.get("multi_use_supported")],
                positions=cart,
            )
        except TaxRule.SaleNotAllowed:
            fees = []
        return Decimal(sum(c.price for c in cart) + sum(f.value for f in fees))

    def _usable(self, pprov, total):
        # Pretix's PaymentStep only offers and accepts providers that are both enabled AND
        # allowed for this cart. Removing that step dropped the second half: the implicit
        # "free" and "boxoffice" providers hard-code is_enabled = True and rely solely on
        # is_allowed() (free: total must be zero; boxoffice: never online), so without it a
        # POST of payment=free placed a paid order and confirmed it as paid.
        return bool(pprov and pprov.is_enabled and pprov.is_allowed(self.request, total=total))

    def patched_post(self, request):
        self.request = request
        payment_id = request.POST.get("payment", "")
        providers = request.event.get_payment_providers()
        total = _order_total(self)

        if payment_id:
            pprov = providers.get(payment_id)
            if _usable(self, pprov, total):
                # Clear any previously selected non-multi-use payments then re-select
                self.cart_session["payments"] = [
                    p for p in self.cart_session.get("payments", []) if p.get("multi_use_supported")
                ]
                resp = pprov.checkout_prepare(request, self.get_cart())
                if isinstance(resp, str):
                    # Provider needs an external redirect before order creation
                    return redirect_to_url(resp)
                elif resp is True:
                    add_payment_to_cart(request, pprov, None, None, None)
                else:
                    # checkout_prepare returned False — form data invalid (e.g. Stripe JS error)
                    return redirect_to_url(self.get_step_url(request))
            else:
                messages.error(request, _lazy("Please select a valid payment method."))
                return redirect_to_url(self.get_step_url(request))
        elif not self.cart_session.get("payments"):
            messages.error(request, _lazy("Please select a payment method."))
            return redirect_to_url(self.get_step_url(request))

        # A selection stored earlier may no longer hold: e.g. "free" picked while the cart
        # was at zero, then a paid ticket added. Pretix's PaymentStep.is_completed re-checks
        # this on every step; with that step gone, do it here before the order is placed.
        if not all(_usable(self, providers.get(p.get("provider")), total) for p in self.cart_session["payments"]):
            self.cart_session["payments"] = []
            messages.error(request, _lazy("Please select a valid payment method."))
            return redirect_to_url(self.get_step_url(request))

        return original_post(self, request)

    ConfirmStep.post = patched_post
    ConfirmStep._autoconfig_post_patched = True


def _patch_event_index_voucher_bypass():
    """
    Make ?voucher= on the styled event index page honor a bypass voucher's
    allow_ignore_quota flag, so buyers don't need to land on the unstyled
    /redeem page for sold-out bypass.

    Pretix's EventIndex.get_context_data calls get_grouped_items WITHOUT a
    voucher argument. That means items are availability-checked against the
    raw quota regardless of any ?voucher= query string, and a sold-out item
    stays sold-out even when the buyer holds a bypass voucher. We re-run
    get_grouped_items with the voucher and overwrite items_by_category /
    display_add_to_cart only when the voucher is valid and has
    allow_ignore_quota set.
    """
    from pretix.base.models import Voucher
    from pretix.presale.views.event import EventIndex, get_grouped_items, item_group_by_category

    if getattr(EventIndex, "_autoconfig_voucher_bypass_patched", False):
        return

    original_get_context_data = EventIndex.get_context_data

    def patched_get_context_data(self, **kwargs):
        ctx = original_get_context_data(self, **kwargs)

        voucher_code = (self.request.GET.get("voucher") or "").strip()
        if not voucher_code:
            return ctx
        if self.request.event.has_subevents and not self.subevent:
            return ctx

        try:
            voucher = self.request.event.vouchers.get(code__iexact=voucher_code)
        except Voucher.DoesNotExist:
            return ctx
        if not voucher.allow_ignore_quota:
            return ctx

        try:
            items, display_add_to_cart = get_grouped_items(
                self.request.event,
                subevent=self.subevent,
                voucher=voucher,
                filter_items=self.request.GET.getlist("item"),
                filter_categories=self.request.GET.getlist("category"),
                require_seat=None,
                channel=self.request.sales_channel,
                memberships=(
                    self.request.customer.usable_memberships(
                        for_event=self.subevent or self.request.event,
                        testmode=self.request.event.testmode,
                    )
                    if getattr(self.request, "customer", None)
                    else None
                ),
            )
        except Exception:
            return ctx

        items = [i for i in items if not i.requires_seat]

        # Quantities are never pre-filled from the link: loading a page must not reserve
        # tickets. What the link covers is stated as text and enforced as a cap on the
        # total instead.
        ctx["autoconfig_link_cap"] = max(0, (voucher.max_usages or 0) - (voucher.redeemed or 0))

        ctx["items_by_category"] = item_group_by_category(items)
        ctx["display_add_to_cart"] = display_add_to_cart
        ctx["itemnum"] = len(items)
        return ctx

    EventIndex.get_context_data = patched_get_context_data
    EventIndex._autoconfig_voucher_bypass_patched = True
