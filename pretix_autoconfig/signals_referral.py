import logging

logger = logging.getLogger(__name__)

_SESSION_KEY = "autoconfig_referral_code"
_SESSION_VOUCHER_KEY = "autoconfig_referral_voucher"


def _scoped_key(base, event):
    return f"{base}_{event.organizer.slug}_{event.slug}"


def _store_referral(request, code, event):
    try:
        request.session[_scoped_key(_SESSION_KEY, event)] = code
        request.session.modified = True
    except Exception:
        logger.debug("Could not store referral code in session", exc_info=True)


def _store_referral_voucher(request, voucher_code, event):
    try:
        request.session[_scoped_key(_SESSION_VOUCHER_KEY, event)] = voucher_code
        request.session.modified = True
    except Exception:
        logger.debug("Could not store referral voucher in session", exc_info=True)


def load_referral_code(request, event):
    try:
        return request.session.get(_scoped_key(_SESSION_KEY, event), "")
    except Exception:
        return ""


def load_referral_voucher(request, event):
    try:
        return request.session.get(_scoped_key(_SESSION_VOUCHER_KEY, event), "")
    except Exception:
        return ""


# Imported lazily to avoid circular imports at module load time; signals are
# resolved inside each handler so the module-level functions stay alive as
# strong references and Django's weakref-based dispatch works correctly.


def capture_referral_on_page_load(sender, request, **kwargs):
    """Store ?ref= and optional ?voucher= from any presale page load into the session."""
    code = request.GET.get("ref", "")
    if code:
        _store_referral(request, code, sender)
    voucher = request.GET.get("voucher", "")
    if voucher:
        _store_referral_voucher(request, voucher, sender)


def capture_referral_on_cart(sender, request=None, **kwargs):
    """Belt-and-suspenders: capture ?ref= or ref POST param during cart submission."""
    if request is not None:
        code = request.GET.get("ref", "") or request.POST.get("ref", "")
        if code:
            _store_referral(request, code, sender)
    return []


def store_referral_in_order_meta(sender, request, **kwargs):
    code = load_referral_code(request, sender)
    if code:
        return {"referral_code": code}
    return {}


def connect_signals():
    from pretix.presale.signals import fee_calculation_for_cart, order_api_meta_from_request, process_request

    process_request.connect(
        capture_referral_on_page_load, dispatch_uid="autoconfig_referral_process_request", weak=False
    )
    fee_calculation_for_cart.connect(
        capture_referral_on_cart, dispatch_uid="autoconfig_referral_capture_cart", weak=False
    )
    order_api_meta_from_request.connect(
        store_referral_in_order_meta, dispatch_uid="autoconfig_referral_order_meta", weak=False
    )


connect_signals()
