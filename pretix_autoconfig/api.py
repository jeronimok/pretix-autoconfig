import hmac
import os
import re

from django.core.exceptions import FieldDoesNotExist
from django.db import transaction
from pretix.base.models import Organizer, Team, TeamAPIToken
from pretix.settings import config
from rest_framework import status, viewsets
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from .signals_config import get_primary_color


def _get_token_name():
    value = os.environ.get("PRETIX_AUTOCONFIG_TOKEN_NAME")
    if value:
        return value
    try:
        from pretix.settings import config

        return config.get("pretix_autoconfig", "token_name", fallback=None) or "Admin"
    except Exception:
        return "Admin"


def _get_provision_secret():
    value = os.environ.get("PRETIX_AUTOCONFIG_PROVISION_SECRET")
    if value:
        return value
    try:
        return config.get("pretix_autoconfig", "provision_secret", fallback=None) or ""
    except Exception:
        return ""


def _team_full_permission_kwargs():
    # Pretix ≥2026 uses all_event_permissions / all_organizer_permissions.
    # Older dev builds used individual can_* BooleanFields. Detect at runtime.
    try:
        Team._meta.get_field("all_event_permissions")
        return {"all_event_permissions": True, "all_organizer_permissions": True}
    except FieldDoesNotExist:
        return {
            "can_create_events": True,
            "can_change_teams": True,
            "can_change_organizer_settings": True,
            "can_manage_customers": True,
            "can_manage_gift_cards": True,
            "can_manage_reusable_media": True,
            "can_change_event_settings": True,
            "can_change_items": True,
            "can_view_orders": True,
            "can_change_orders": True,
            "can_checkin_orders": True,
            "can_view_vouchers": True,
            "can_change_vouchers": True,
        }


def _slug_from_name(name):
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:50] or "organizer"


class ProvisionSecretAuthentication(BaseAuthentication):
    def authenticate(self, request):
        secret = request.headers.get("X-Provision-Secret", "")
        if not secret:
            raise AuthenticationFailed("X-Provision-Secret header is required.")
        expected = _get_provision_secret()
        if not expected:
            raise AuthenticationFailed("Provision secret not configured.")
        if not hmac.compare_digest(secret, expected):
            raise AuthenticationFailed("Invalid provision secret.")
        return (None, None)


class ProvisionRateThrottle(SimpleRateThrottle):
    """Defense in depth for provision-organizer, independent of any network-level gate.

    Hardcodes the rate rather than reading DRF's global THROTTLE_RATES, since this
    plugin shouldn't assume it can add config to the host Pretix install's REST_FRAMEWORK
    settings. Keyed by request IP (get_ident) since auth here is a shared secret, not a
    DRF user.
    """

    scope = "autoconfig_provision"

    def get_rate(self):
        return "5/hour"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class OrganizerProvisionViewSet(viewsets.ViewSet):
    authentication_classes = [ProvisionSecretAuthentication]
    permission_classes = []
    throttle_classes = [ProvisionRateThrottle]

    def create(self, request):
        name = request.data.get("name", "").strip()
        slug = request.data.get("slug", "").strip() or _slug_from_name(name)

        if not name:
            return Response({"name": "Required."}, status=status.HTTP_400_BAD_REQUEST)
        if Organizer.objects.filter(slug=slug).exists():
            return Response(
                {"slug": f"Organizer with slug '{slug}' already exists."},
                status=status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            organizer = Organizer.objects.create(name=name, slug=slug)
            primary_color = get_primary_color()
            if primary_color:
                organizer.settings.set("primary_color", primary_color)
            team = Team.objects.create(
                organizer=organizer,
                name="Administrators",
                all_events=True,
                **_team_full_permission_kwargs(),
            )
            token_obj = TeamAPIToken.objects.create(team=team, name=_get_token_name())

        return Response(
            {"organizer_slug": organizer.slug, "api_token": token_obj.token},
            status=status.HTTP_201_CREATED,
        )
