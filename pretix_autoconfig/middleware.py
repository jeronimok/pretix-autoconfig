from django.http import HttpResponseRedirect
from django.urls import Resolver404, resolve

from pretix_autoconfig.signals_config import get_favicon_url, get_platform_url

_CHECKOUT_BREAKOUT_SCRIPT = (
    b"<script>if(window.self!==window.top){window.top.location.href=window.location.href;}</script>"
)

# Routes Pretix itself considers safe to embed (pretix/presale/urls.py: frame_wrapped_urls — its own
# docstring explains the split exists "to prevent all clickjacking and CSRF attacks that would
# otherwise be possible"). Order management, account, and OAuth2/OIDC pages are deliberately excluded
# by Pretix and must keep the default X-Frame-Options: DENY.
try:
    from pretix.presale.urls import frame_wrapped_urls as _frame_wrapped_urls

    _FRAME_SAFE_URL_NAMES = {p.name for p in _frame_wrapped_urls if getattr(p, "name", None)}
except ImportError:
    # Fallback if a Pretix upgrade ever restructures presale/urls.py — keeps this narrow rather than
    # silently reverting to "strip X-Frame-Options everywhere outside /control/".
    _FRAME_SAFE_URL_NAMES = {
        "event.cart.remove",
        "event.cart.voucher",
        "event.cart.clear",
        "event.cart.extend",
        "event.cart.download.answer",
        "event.checkout.start",
        "event.checkout",
        "event.redeem",
        "event.seatingplan",
        "event.waitinglist",
        "event.waitinglist.remove",
        "event.index",
    }


class AllowShopIframe:
    """Remove X-Frame-Options from the shop/checkout pages Pretix itself considers embed-safe.

    Django's XFrameOptionsMiddleware sets X-Frame-Options: DENY globally. We strip it only for
    routes in _FRAME_SAFE_URL_NAMES (event index, cart, checkout, redeem, seatingplan, waitinglist)
    so organizers can embed their ticket shop in iframes on external websites. Account pages and
    OAuth2/OIDC endpoints keep the default DENY — those were never part of the embed feature.
    (Order-management pages like cancel/pay/change are separately exempted by Pretix core itself
    via @xframe_options_exempt in presale/views/order.py, independent of this middleware either way —
    narrowing the safe-list here is still correct as a second layer, it just isn't what protects them.)

    On checkout pages, we inject a script that breaks out of the iframe so Stripe's
    payment flow runs full-page. Stripe detects window.self !== window.top and blocks
    redirect-based payment methods when embedded; the breakout prevents that.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith("/control/"):
            url_name = getattr(request.resolver_match, "url_name", None)
            if url_name in _FRAME_SAFE_URL_NAMES:
                response.headers.pop("X-Frame-Options", None)
            elif url_name is None and self._is_frame_safe_early_response(request, response):
                response.headers.pop("X-Frame-Options", None)
            self._inject_head(response, request)
        return response

    def _is_frame_safe_early_response(self, request, response):
        """Whether a response produced before URL resolution is still safe to embed.

        A shop that is not live never reaches its view: Pretix answers 403 from `_detect_event`
        during request middleware (presale/utils.py), so Django never sets
        `request.resolver_match` and the check above cannot recognise the route. The visible
        result was that an embedded shop showed the browser's "refused to connect" error the
        moment an organizer took it offline, even though opening the same URL directly serves a
        perfectly good branded "Shop offline" page.

        Resolving the path by hand recovers the route name, so the same narrow safe-list still
        decides. Staff are excluded because Pretix's stock offline template renders a
        `control:user.sudo` form ("Admin mode") for them, which must not be clickjackable. Our
        own template override drops that form entirely, but the guard keeps this correct if the
        override is ever removed.
        """
        if response.status_code != 403:
            return False
        if getattr(request.user, "is_staff", False):
            return False
        try:
            match = resolve(request.path_info, urlconf=getattr(request, "urlconf", None))
        except Resolver404:
            return False
        return match.url_name in _FRAME_SAFE_URL_NAMES

    def _inject_head(self, response, request):
        if "text/html" not in response.headers.get("Content-Type", ""):
            return
        if response.has_header("Content-Encoding"):
            return
        if not hasattr(response, "content"):
            return

        # Django's HttpResponse.content setter does not touch Content-Length (verified against
        # Django 5.2's source -- it only replaces the internal buffer). Every reassignment below
        # therefore has to be paired with recomputing the header ourselves, or the response ships
        # with a Content-Length from before our edit while the body is the edited, differently
        # sized one. A client reads exactly Content-Length bytes and stops: if the real body is
        # shorter, the client is left expecting more that never arrives, which a proxy in between
        # reports as the upstream having "prematurely closed" the connection. That is exactly what
        # removing pretix's four PNG favicon links (2026-09-04) surfaced in production -- shrinking
        # the body by ~450 bytes against a stale, larger Content-Length broke every single page
        # load with a favicon configured. The single .ico links the code stripped before that had
        # a small enough delta not to trip it, and the checkout breakout script below has the same
        # bug in the opposite direction (adds bytes, so a stale *smaller* Content-Length truncates
        # what the client reads) -- also fixed here, not just the favicon path.
        mutated = False

        inject = b""

        if "/checkout/" in request.path:
            inject += _CHECKOUT_BREAKOUT_SCRIPT

        if inject and b"</head>" in response.content:
            response.content = response.content.replace(b"</head>", inject + b"</head>", 1)
            mutated = True

        # Replace pretix's default favicon links with the configured one, so the two do not
        # compete. Unset means pretix keeps its own favicon.
        #
        # Matching on "pretixbase/img/" rather than "pretixbase/img/favicon" specifically: pretix's
        # base.html (in the branch used when the organizer has not uploaded a custom
        # settings.favicon) emits six default links -- rel=icon/shortcut-icon pointing at
        # img/favicon.ico, plus four PNG variants under img/icons/ (16x16, 32x32, 192x192,
        # apple-touch-icon). The old literal matched the two .ico links but not the four under
        # icons/, since "img/" and "favicon" are no longer adjacent there -- so those four survived
        # untouched and a browser could still pick pretix's icon over ours. An organizer-uploaded
        # settings.favicon renders from a media/ path, not static/pretixbase/img/, so this still only
        # fires against pretix's own bundled default, which is the intended scope either way.
        favicon_url = get_favicon_url()
        if favicon_url and b"pretixbase/img/" in response.content:
            our_links = (
                f'<link rel="icon" href="{favicon_url}" type="image/svg+xml">'
                f'<link rel="shortcut icon" href="{favicon_url}">'
            ).encode()
            import re as _re

            response.content = _re.sub(
                rb"<link[^>]+pretixbase/img/[^>]+>",
                b"",
                response.content,
            )
            response.content = response.content.replace(b"</head>", our_links + b"</head>", 1)
            mutated = True

        if mutated:
            response.headers["Content-Length"] = str(len(response.content))


class RedirectBareRootToMarketing:
    """Send a bare visit to the platform root to the marketing site instead of Pretix's own.

    Pretix's stock root view (`pretixpresale/index.html`) explains "this is a self-hosted
    installation of pretix, your free and open source ticket sales software" and links to
    `/control/`. Accurate, but not something a buyer or a curious visitor typing
    the ticket domain directly should ever see -- nobody buys a ticket at the bare root, and
    anyone who lands there wants either the marketing site or an event they mistyped the URL for.

    Only fires for the exact root path, and only if PRETIX_AUTOCONFIG_PLATFORM_URL is configured
    (unset means: leave Pretix's own page showing, matching every other autoconfig behaviour that
    depends on optional config). Safe to match "/" unconditionally: this Pretix instance has no
    per-organizer custom domains (the KnownDomain table is empty in production), so "/" means
    Pretix's own landing page on every domain this instance serves -- nothing else could be there.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/":
            marketing_url = get_platform_url()
            if marketing_url:
                return HttpResponseRedirect(marketing_url)
        return self.get_response(request)


class KeepLinkOnShopRedirect:
    """Keep the private link in the URL when checkout bounces the buyer back to the shop.

    Pretix redirects from checkout to the bare shop URL when it refuses a step (a cart
    problem, "Your cart is empty", booking period over). Without `?voucher=` a private-link
    buyer then sees the public view: their sold-out tickets as "in your cart" and "SOLD OUT"
    with no controls. Adds the `voucher` and `ref` the buyer arrived with (stored in the
    session by `signals_referral`) to exactly those redirects; any other response is left
    alone. Spec 2026-09-23-private-link-combined-cap.
    """

    _REDIRECTS = (301, 302, 303, 307, 308)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code not in self._REDIRECTS or getattr(request, "event", None) is None:
            return response
        try:
            if not (resolve(request.path_info).url_name or "").startswith("event.checkout"):
                return response
        except Resolver404:
            return response
        return self._with_link(request, response)

    @staticmethod
    def _with_link(request, response):
        from urllib.parse import parse_qsl, urlencode, urlsplit

        from pretix.multidomain.urlreverse import eventreverse

        from pretix_autoconfig.signals_referral import load_referral_code, load_referral_voucher

        location = urlsplit(response.get("Location", ""))
        if location.path != urlsplit(eventreverse(request.event, "presale:event.index")).path:
            return response
        # Pretix adds its own parameters (e.g. require_cookie=true); keep them, add ours.
        query = parse_qsl(location.query, keep_blank_values=True)
        if any(key == "voucher" for key, _ in query):
            return response
        link = [
            (k, v)
            for k, v in (
                ("voucher", load_referral_voucher(request, request.event)),
                ("ref", load_referral_code(request, request.event)),
            )
            if v and not any(key == k for key, _ in query)
        ]
        if link:
            response["Location"] = location._replace(query=urlencode(link + query)).geturl()
        return response
