from unittest.mock import patch

import pytest
from django.core.cache import cache
from pretix.base.models import Organizer, Team, TeamAPIToken
from rest_framework.test import APIRequestFactory

from pretix_autoconfig.api import OrganizerProvisionViewSet, _slug_from_name

MOCK_SECRET = "test-secret-abc123"
_view = OrganizerProvisionViewSet.as_view({"post": "create"})


@pytest.fixture(autouse=True)
def _clear_provision_secret_env(monkeypatch):
    # The env var wins over pretix.cfg, so a developer machine that has it exported would
    # otherwise override the patched config value these tests rely on.
    monkeypatch.delenv("PRETIX_AUTOCONFIG_PROVISION_SECRET", raising=False)


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    # ProvisionRateThrottle counts requests in the cache; without this, tests in this
    # module would trip each other's rate limit since they share an IP-keyed bucket.
    cache.clear()
    yield
    cache.clear()


def _request(data, secret=MOCK_SECRET, remote_addr="127.0.0.1"):
    factory = APIRequestFactory()
    return factory.post(
        "/api/v1/autoconfig/provision-organizer/",
        data=data,
        format="json",
        HTTP_X_PROVISION_SECRET=secret,
        REMOTE_ADDR=remote_addr,
    )


def _post(data, secret=MOCK_SECRET, remote_addr="127.0.0.1"):
    with patch("pretix_autoconfig.api.config.get", return_value=MOCK_SECRET):
        return _view(_request(data, secret=secret, remote_addr=remote_addr))


# ---------------------------------------------------------------------------
# _slug_from_name helper
# ---------------------------------------------------------------------------


class TestSlugFromName:
    def test_lowercases(self):
        assert _slug_from_name("Sound Avenue") == "sound-avenue"

    def test_replaces_spaces_with_hyphens(self):
        assert _slug_from_name("My Org Name") == "my-org-name"

    def test_strips_special_chars(self):
        assert _slug_from_name("Org & Co.!") == "org-co"

    def test_strips_leading_trailing_hyphens(self):
        assert _slug_from_name("  My Org  ") == "my-org"

    def test_truncates_to_50_chars(self):
        assert len(_slug_from_name("a" * 60)) <= 50

    def test_empty_name_returns_fallback(self):
        assert _slug_from_name("!!!") == "organizer"


# ---------------------------------------------------------------------------
# OrganizerProvisionViewSet
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOrganizerProvisionViewSet:
    def test_valid_request_returns_201(self):
        response = _post({"name": "Sound Avenue"})
        assert response.status_code == 201

    def test_valid_request_returns_slug_and_token(self):
        response = _post({"name": "Sound Avenue"})
        assert response.data["organizer_slug"] == "sound-avenue"
        assert "api_token" in response.data

    def test_valid_request_creates_organizer(self):
        _post({"name": "Sound Avenue"})
        assert Organizer.objects.filter(slug="sound-avenue").exists()

    def test_valid_request_creates_admin_team(self):
        _post({"name": "Sound Avenue"})
        org = Organizer.objects.get(slug="sound-avenue")
        team = Team.objects.get(organizer=org)
        assert team.all_events is True
        assert team.can_create_events is True
        assert team.can_change_teams is True

    def test_valid_request_creates_api_token(self):
        response = _post({"name": "Sound Avenue"})
        org = Organizer.objects.get(slug="sound-avenue")
        token = TeamAPIToken.objects.get(team__organizer=org)
        assert token.token == response.data["api_token"]

    def test_explicit_slug_is_used(self):
        response = _post({"name": "Sound Avenue", "slug": "custom-slug"})
        assert response.status_code == 201
        assert Organizer.objects.filter(slug="custom-slug").exists()

    def test_duplicate_slug_returns_409(self):
        Organizer.objects.create(name="Existing", slug="sound-avenue")
        response = _post({"name": "Sound Avenue"})
        assert response.status_code == 409

    def test_missing_name_returns_400(self):
        response = _post({"slug": "some-slug"})
        assert response.status_code == 400

    def test_wrong_secret_returns_403(self):
        response = _post({"name": "Sound Avenue"}, secret="wrong-secret")
        assert response.status_code == 403

    def test_missing_secret_returns_403(self):
        response = _post({"name": "Sound Avenue"}, secret="")
        assert response.status_code == 403

    def test_env_var_secret_is_accepted(self, monkeypatch):
        # The env var takes precedence over pretix.cfg, matching every other setting.
        monkeypatch.setenv("PRETIX_AUTOCONFIG_PROVISION_SECRET", "env-secret-xyz")
        with patch("pretix_autoconfig.api.config.get", return_value=""):
            response = _view(_request({"name": "Sound Avenue"}, secret="env-secret-xyz"))
        assert response.status_code == 201

    def test_unconfigured_secret_returns_403(self, monkeypatch):
        monkeypatch.delenv("PRETIX_AUTOCONFIG_PROVISION_SECRET", raising=False)
        with patch("pretix_autoconfig.api.config.get", return_value=""):
            response = _view(_request({"name": "Sound Avenue"}, secret="anything"))
        assert response.status_code == 403

    def test_unconfigured_primary_color_still_provisions(self):
        # Production had no primary_color in pretix.cfg, so get_primary_color() returned
        # None and hierarkey refused to serialize it, 500ing the whole request.
        with patch("pretix_autoconfig.api.get_primary_color", return_value=None):
            response = _post({"name": "Sound Avenue"})
        assert response.status_code == 201
        org = Organizer.objects.get(slug="sound-avenue")
        assert TeamAPIToken.objects.filter(team__organizer=org).exists()

    def test_configured_primary_color_is_stored(self):
        with patch("pretix_autoconfig.api.get_primary_color", return_value="#FF6B6B"):
            _post({"name": "Sound Avenue"})
        org = Organizer.objects.get(slug="sound-avenue")
        assert org.settings.get("primary_color") == "#FF6B6B"

    def test_failure_after_organizer_create_leaves_no_orphan(self):
        # The original 500 left an organizer row with no team and no token, so retrying
        # the same slug returned 409 instead of succeeding.
        with patch("pretix_autoconfig.api.TeamAPIToken.objects.create", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                _post({"name": "Sound Avenue"})
        assert not Organizer.objects.filter(slug="sound-avenue").exists()


# ---------------------------------------------------------------------------
# ProvisionRateThrottle
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProvisionRateThrottle:
    @pytest.fixture(autouse=True)
    def _real_cache(self, settings):
        # Pretix's test settings force CACHES['default'] to a DummyCache that never
        # stores anything, so the throttle's request-count bucket needs a real cache
        # backend here to be exercisable at all. Production always has redis backing
        # 'default' (see pretix/settings.py), so this only patches the test environment.
        settings.CACHES = {
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "provision-throttle"}
        }

    def test_sixth_request_in_window_is_throttled(self):
        for i in range(5):
            response = _post({"name": f"Org {i}"})
            assert response.status_code == 201
        response = _post({"name": "Org 6"})
        assert response.status_code == 429

    def test_different_ips_are_throttled_independently(self):
        for i in range(5):
            response = _post({"name": f"Org {i}"}, remote_addr="10.0.0.1")
            assert response.status_code == 201
        response = _post({"name": "Org from another ip"}, remote_addr="10.0.0.2")
        assert response.status_code == 201
