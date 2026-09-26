from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import ResolverMatch

from pretix_autoconfig.middleware import AllowShopIframe, RedirectBareRootToMarketing


def _response_for(url_name, path="/some-org/some-event/"):
    factory = RequestFactory()
    request = factory.get(path)
    request.resolver_match = ResolverMatch(func=lambda r: None, args=(), kwargs={}, url_name=url_name)

    def get_response(req):
        resp = HttpResponse(b"<html><head></head><body></body></html>", content_type="text/html")
        resp.headers["X-Frame-Options"] = "DENY"
        return resp

    middleware = AllowShopIframe(get_response)
    return middleware(request)


class TestAllowShopIframe:
    def test_event_index_loses_xframe_header(self):
        response = _response_for("event.index")
        assert "X-Frame-Options" not in response.headers

    def test_checkout_loses_xframe_header(self):
        response = _response_for("event.checkout", path="/some-org/some-event/checkout/confirm/")
        assert "X-Frame-Options" not in response.headers

    def test_cart_clear_loses_xframe_header(self):
        response = _response_for("event.cart.clear")
        assert "X-Frame-Options" not in response.headers

    def test_order_cancel_do_keeps_xframe_header(self):
        response = _response_for("event.order.cancel.do", path="/some-org/some-event/order/ABCDE/somesecret/cancel/do")
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_order_pay_change_keeps_xframe_header(self):
        response = _response_for(
            "event.order.pay.change", path="/some-org/some-event/order/ABCDE/somesecret/pay/change"
        )
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_account_password_keeps_xframe_header(self):
        response = _response_for("organizer.customer.password", path="/some-org/account/password")
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_control_panel_keeps_xframe_header_regardless_of_url_name(self):
        response = _response_for("event.index", path="/control/events/")
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_no_resolver_match_keeps_xframe_header(self):
        factory = RequestFactory()
        request = factory.get("/some-org/some-event/")
        request.resolver_match = None

        def get_response(req):
            resp = HttpResponse(b"<html><head></head><body></body></html>", content_type="text/html")
            resp.headers["X-Frame-Options"] = "DENY"
            return resp

        response = AllowShopIframe(get_response)(request)
        assert response.headers["X-Frame-Options"] == "DENY"


def _early_403_response(path="/some-org/some-event/", is_staff=False, status=403):
    """A response produced before URL resolution, as Pretix does for an offline shop.

    `_detect_event` answers from request middleware, so `resolver_match` is never set.
    """
    factory = RequestFactory()
    request = factory.get(path)
    request.resolver_match = None
    request.user = SimpleNamespace(is_staff=is_staff)

    def get_response(req):
        resp = HttpResponse(b"<html><head></head><body></body></html>", content_type="text/html", status=status)
        resp.headers["X-Frame-Options"] = "DENY"
        return resp

    return AllowShopIframe(get_response)(request)


class TestOfflineShopFraming:
    """An offline shop must still be embeddable, or an embed shows "refused to connect"."""

    def test_offline_event_index_loses_xframe_header(self):
        response = _early_403_response()
        assert "X-Frame-Options" not in response.headers

    def test_staff_keeps_xframe_header(self):
        # Pretix's stock offline template renders a control:user.sudo form for staff.
        response = _early_403_response(is_staff=True)
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_non_403_keeps_xframe_header(self):
        response = _early_403_response(status=200)
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_unresolvable_path_keeps_xframe_header(self):
        response = _early_403_response(path="/this/is/not/a/shop/url/at/all/")
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_order_page_keeps_xframe_header(self):
        # Order management is deliberately excluded from the frame-safe list.
        response = _early_403_response(path="/some-org/some-event/order/ABCDE/somesecret/")
        assert response.headers["X-Frame-Options"] == "DENY"


def _html_response_for(body, favicon_url=None, path="/some-org/some-event/"):
    factory = RequestFactory()
    request = factory.get(path)
    request.resolver_match = ResolverMatch(func=lambda r: None, args=(), kwargs={}, url_name="event.index")

    def get_response(req):
        return HttpResponse(body, content_type="text/html")

    with patch("pretix_autoconfig.middleware.get_favicon_url", return_value=favicon_url):
        return AllowShopIframe(get_response)(request)


# pretix's default (non-custom-favicon) branch of presale/templates/pretixpresale/base.html: two
# rel=icon/shortcut-icon links to a hashed favicon.ico, plus four PNG variants under img/icons/.
_PRETIX_DEFAULT_FAVICON_HEAD = b"""<html><head><title>x</title>
<link rel="icon" href="/static/pretixbase/img/favicon.5d41402abc4b.ico">
<link rel="shortcut icon" href="/static/pretixbase/img/favicon.5d41402abc4b.ico">
<link rel="icon" type="image/png" sizes="16x16" href="/static/pretixbase/img/icons/favicon-16x16.d9c6017a4222.png">
<link rel="icon" type="image/png" sizes="32x32" href="/static/pretixbase/img/icons/favicon-32x32.5066e55d61de.png">
<link rel="icon" type="image/png" sizes="192x192"
      href="/static/pretixbase/img/icons/android-chrome-192x192.7d21acc539fe.png">
<link rel="apple-touch-icon" sizes="180x180" href="/static/pretixbase/img/icons/apple-touch-icon.332c189bb6df.png">
</head><body></body></html>"""


class TestFaviconReplacement:
    """pretix always renders six favicon <link> tags unless the organizer has uploaded their own
    settings.favicon (which lives under media/, not static/pretixbase/img/, and is left alone).
    """

    def test_replaces_all_six_default_favicon_links(self):
        response = _html_response_for(_PRETIX_DEFAULT_FAVICON_HEAD, favicon_url="https://x/favicon.svg")
        assert b"pretixbase/img/" not in response.content

    def test_icons_subpath_variants_are_removed(self):
        # Regression: these four survived the old literal "pretixbase/img/favicon" match, since
        # "img/" and "favicon" are no longer adjacent once pretix moved them under img/icons/.
        response = _html_response_for(_PRETIX_DEFAULT_FAVICON_HEAD, favicon_url="https://x/favicon.svg")
        for name in ("favicon-16x16", "favicon-32x32", "android-chrome-192x192", "apple-touch-icon"):
            assert name.encode() not in response.content, f"{name} link was not stripped"

    def test_injects_our_favicon_links(self):
        response = _html_response_for(_PRETIX_DEFAULT_FAVICON_HEAD, favicon_url="https://x/favicon.svg")
        assert response.content.count(b"https://x/favicon.svg") == 2

    def test_no_favicon_url_leaves_pretix_links_untouched(self):
        response = _html_response_for(_PRETIX_DEFAULT_FAVICON_HEAD, favicon_url=None)
        assert response.content.count(b"pretixbase/img/") == 6

    def test_organizer_custom_favicon_is_left_alone(self):
        # settings.favicon renders from media/, not static/pretixbase/img/ -- this middleware
        # should not touch it even when favicon_url is configured.
        body = (
            b'<html><head><link rel="icon" href="/media/pub/org/event/favicon.thumb_16x16.jpg">'
            b"</head><body></body></html>"
        )
        response = _html_response_for(body, favicon_url="https://x/favicon.svg")
        assert b"favicon.thumb_16x16.jpg" in response.content
        assert b"https://x/favicon.svg" not in response.content


class TestContentLengthStaysAccurate:
    """A P0 production incident (2026-09-04): Django's HttpResponse.content setter does not
    touch Content-Length (verified against Django 5.2's own source). Every response mutation in
    _inject_head has to update the header itself, or a client reads exactly the stale
    Content-Length and is left expecting bytes that never arrive -- which a proxy in between
    reports as the upstream having closed the connection early. Removing pretix's four PNG
    favicon links shrank the body enough to trip this on every single page load with a favicon
    configured; the checkout breakout script has the identical bug in the opposite direction.
    """

    def test_favicon_replacement_keeps_content_length_accurate(self):
        response = _html_response_for(_PRETIX_DEFAULT_FAVICON_HEAD, favicon_url="https://x/favicon.svg")
        assert int(response.headers["Content-Length"]) == len(response.content)

    def test_checkout_breakout_script_keeps_content_length_accurate(self):
        body = b"<html><head><title>x</title></head><body>checkout</body></html>"
        response = _html_response_for(body, favicon_url=None, path="/some-org/some-event/checkout/confirm/")
        assert int(response.headers["Content-Length"]) == len(response.content)

    def test_both_mutations_together_keep_content_length_accurate(self):
        body = _PRETIX_DEFAULT_FAVICON_HEAD.replace(b"<body></body>", b"<body>checkout</body>")
        response = _html_response_for(
            body, favicon_url="https://x/favicon.svg", path="/some-org/some-event/checkout/confirm/"
        )
        assert int(response.headers["Content-Length"]) == len(response.content)

    def test_untouched_response_is_not_given_a_content_length_it_lacked(self):
        # No favicon_url and not a checkout path: neither mutation fires, so the header should be
        # left exactly as pretix's own response produced it, not force-added.
        response = _html_response_for(_PRETIX_DEFAULT_FAVICON_HEAD, favicon_url=None)
        assert response.has_header("Content-Length") == HttpResponse(_PRETIX_DEFAULT_FAVICON_HEAD).has_header(
            "Content-Length"
        )


class TestRedirectBareRootToMarketing:
    """Pretix's stock root page explains "this is a self-hosted installation of pretix" and
    links to /control/ -- fine for an admin, not something a random visitor or a buyer who
    mistyped a URL should see.
    """

    def _response_for(self, path, marketing_url="https://example.com"):
        factory = RequestFactory()
        request = factory.get(path)

        def get_response(req):
            return HttpResponse(b"pretix's own root page", content_type="text/html")

        with patch("pretix_autoconfig.middleware.get_platform_url", return_value=marketing_url):
            return RedirectBareRootToMarketing(get_response)(request)

    def test_bare_root_redirects_to_the_marketing_site(self):
        response = self._response_for("/")
        assert response.status_code == 302
        assert response.headers["Location"] == "https://example.com"

    def test_event_shop_is_not_redirected(self):
        response = self._response_for("/some-org/some-event/")
        assert response.status_code == 200
        assert response.content == b"pretix's own root page"

    def test_control_panel_is_not_redirected(self):
        response = self._response_for("/control/")
        assert response.status_code == 200

    def test_no_platform_url_configured_leaves_pretix_page_showing(self):
        """Fail open, matching every other autoconfig behaviour gated on optional config: unset
        means don't touch anything, not redirect to an empty URL.
        """
        response = self._response_for("/", marketing_url=None)
        assert response.status_code == 200
        assert response.content == b"pretix's own root page"
