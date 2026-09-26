import re

from django.dispatch import receiver
from django.templatetags.static import static
from django.utils.safestring import mark_safe
from pretix.base.middleware import _merge_csp, _parse_csp, _render_csp
from pretix.presale.signals import html_head, process_response

BRANDING_DEFAULTS = {
    "primary_color": "#ff6b6b",
    "autoconfig_branding_canvas_bg": "#ececec",
    "autoconfig_branding_canvas_text": "#111111",
    "autoconfig_branding_card_bg": "#ffffff",
    "autoconfig_branding_card_text": "#111111",
}

CSS_VAR_NAMES = {
    "primary_color": "--autoconfig-brand",
    "autoconfig_branding_canvas_bg": "--autoconfig-canvas-bg",
    "autoconfig_branding_canvas_text": "--autoconfig-canvas-text",
    "autoconfig_branding_card_bg": "--autoconfig-card-bg",
    "autoconfig_branding_card_text": "--autoconfig-card-text",
}

# Overrides for the un-branded neutral ramp (borders, secondary text,
# calendar-block colors) when the organizer enables dark mode. The user's
# five explicit branding colors are NOT touched here — those remain whatever
# the organizer chose.
DARK_MODE_VARS = {
    "--autoconfig-border": "#3a3f47",
    "--autoconfig-text-strong": "#d1d5db",
    "--autoconfig-text-muted": "#a3a3a3",
    "--autoconfig-text-faint": "#7a7f88",
    "--autoconfig-on-dark-bg": "#e5e7eb",
    "--autoconfig-on-dark-text": "#111111",
}

# In dark mode, switch solid brand-color buttons to an outlined style
# (transparent fill, brand-color border and text). Hover fills them in.
# Rules are only emitted when dark mode is on. Every selector is prefixed
# with `html ` to add one extra type-specificity point — shop.css uses
# `!important` for these properties, and our inline <style> is parsed
# before the shop.css <link>, so we need to beat it on specificity.
DARK_MODE_BUTTON_CSS = (
    "html .autoconfig-pill-btn,html .autoconfig-pill-btn:visited,"
    "html .autoconfig-ticket-rows .input-item-count-inc,"
    "html .autoconfig-cart-box .cart .checkout-button-primary .btn.btn-primary,"
    "html .autoconfig-cart-box .cart .checkout-button-primary .btn.btn-primary:visited,"
    "html .btn.btn-primary,html .btn.btn-primary.btn-lg{"
    "background:transparent!important;"
    "color:var(--autoconfig-brand)!important;"
    "border:1.5px solid var(--autoconfig-brand)!important;"
    "box-sizing:border-box;"
    "}"
    "html .autoconfig-pill-btn:hover,html .autoconfig-pill-btn:focus,"
    "html .autoconfig-ticket-rows .input-item-count-inc:hover,"
    "html .autoconfig-cart-box .cart .checkout-button-primary .btn.btn-primary:hover,"
    "html .btn.btn-primary:hover,html .btn.btn-primary:focus,"
    "html .btn.btn-primary.btn-lg:hover,html .btn.btn-primary.btn-lg:focus{"
    "background:var(--autoconfig-brand)!important;"
    "color:var(--autoconfig-on-brand-text)!important;"
    "border-color:var(--autoconfig-brand)!important;"
    "}"
)

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _safe_hex(value, fallback):
    if isinstance(value, str) and HEX_RE.match(value):
        return value
    return fallback


@receiver(html_head, dispatch_uid="autoconfig_branding_inline_style")
def emit_branding_variables(sender, request, **kwargs):
    organizer_settings = sender.organizer.settings
    decls = []
    for key, default in BRANDING_DEFAULTS.items():
        value = _safe_hex(organizer_settings.get(key), default)
        decls.append(f"{CSS_VAR_NAMES[key]}:{value};")
    dark_mode = organizer_settings.get("autoconfig_branding_dark_mode", as_type=bool, default=False)
    if dark_mode:
        for var_name, value in DARK_MODE_VARS.items():
            decls.append(f"{var_name}:{value};")
    # Use `html:root` (specificity 0,1,1) so this rule beats shop.css's
    # `:root` (0,0,1) fallback block even though shop.css loads later.
    root_block = f"html:root{{{''.join(decls)}}}"
    # Emit canvas bg as an explicit property on html+body so the background
    # fills the full viewport even in iframe context (where static file
    # caching can cause shop.css to lag behind or the body to render short).
    canvas_bg = _safe_hex(
        organizer_settings.get("autoconfig_branding_canvas_bg"),
        BRANDING_DEFAULTS["autoconfig_branding_canvas_bg"],
    )
    # html.in-iframe body (0,1,2) beats Pretix's .in-iframe body (0,1,1) which
    # forces white background when the page detects it's embedded in an iframe.
    canvas_rule = (
        f"html,body{{background:{canvas_bg}!important;min-height:100%;}}"
        f"html.in-iframe body{{background:{canvas_bg}!important;}}"
    )
    extra = DARK_MODE_BUTTON_CSS if dark_mode else ""
    # Hoist shop.css into <head> globally so every presale page picks it
    # up — not just the four templates that used to link it themselves.
    # Avoids FOUC and removes the per-template link duplication.
    shop_css = f'<link rel="stylesheet" type="text/css" href="{static("pretix_autoconfig/shop.css")}">'
    return mark_safe(f"{shop_css}<style>{root_block}{canvas_rule}{extra}</style>")


@receiver(process_response, dispatch_uid="autoconfig_branding_csp_unsafe_inline")
def allow_inline_branding_style_csp(sender, request, response, **kwargs):
    """Allow inline <style> in presale CSP so the branding block above renders.

    The branding values are organizer-controlled but constrained to hex colors
    via the form/serializer validators, so the inline content is not user-input.
    """
    if "Content-Security-Policy" in response:
        h = _parse_csp(response["Content-Security-Policy"])
    else:
        h = {}
    _merge_csp(h, {"style-src": ["'unsafe-inline'"]})
    if h:
        response["Content-Security-Policy"] = _render_csp(h)
    return response
