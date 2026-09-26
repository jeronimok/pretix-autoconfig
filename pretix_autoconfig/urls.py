from django.urls import path

from .api import OrganizerProvisionViewSet
from .api_private_links import PrivateLinkHoldsViewSet
from .views_cart import cart_json

_provision_view = OrganizerProvisionViewSet.as_view({"post": "create"})
_holds_list = PrivateLinkHoldsViewSet.as_view({"get": "list"})
_holds_release = PrivateLinkHoldsViewSet.as_view({"post": "release"})

urlpatterns = [
    path("api/v1/autoconfig/provision-organizer/", _provision_view),
    # Cart holds are not in Pretix's REST API, so the platform cannot otherwise tell a link
    # that is free from one frozen in an abandoned cart.
    path(
        "api/v1/organizers/<str:organizer>/autoconfig/private-link-holds/",
        _holds_list,
        name="private-link-holds",
    ),
    path(
        "api/v1/organizers/<str:organizer>/autoconfig/private-link-holds/release/",
        _holds_release,
        name="private-link-holds-release",
    ),
    # Deliberately a root pattern with the organizer/event prefix spelled out, not an
    # `event_patterns` entry: those are wrapped in require_plugin and 404 on events that
    # do not list pretix_autoconfig, while our shop templates (and the JS that calls this)
    # render for every event. Plugin patterns are matched before Pretix's own presale
    # patterns (multidomain/maindomain_urlconf.py), so this more specific path wins.
    path(
        "<str:organizer>/<str:event>/autoconfig/cart.json",
        cart_json,
        name="cart.json",
    ),
]
