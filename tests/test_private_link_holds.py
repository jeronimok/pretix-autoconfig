"""Guards for the private-link holds endpoint.

It reports cart reservations Pretix's REST API does not expose, and can throw them away.
Both halves are dangerous if scoping slips, so the tests here are mostly about who may
call it and what it can reach. See ADR 0004.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.exceptions import NotFound, PermissionDenied

from pretix_autoconfig.api_private_links import _event_for, _token_team

SOURCE = (Path(__file__).resolve().parent.parent / "pretix_autoconfig" / "api_private_links.py").read_text()


def _request(team=None):
    return SimpleNamespace(auth=SimpleNamespace(team=team) if team else None)


def _team(organizer_slug="org-a", all_events=True, **perms):
    team = SimpleNamespace(
        organizer=SimpleNamespace(slug=organizer_slug),
        all_events=all_events,
        limit_events=MagicMock(),
        can_view_orders=perms.get("can_view_orders", True),
        can_change_orders=perms.get("can_change_orders", True),
    )
    team.limit_events.filter.return_value.exists.return_value = False
    return team


class TestAuthorisation:
    def test_anonymous_request_is_refused(self):
        with pytest.raises(PermissionDenied):
            _token_team(_request())

    def test_token_from_another_organizer_is_refused(self):
        with pytest.raises(PermissionDenied):
            _event_for(_request(_team("org-a")), "org-b", "some-event", "can_view_orders")

    def test_token_without_the_permission_is_refused(self):
        team = _team("org-a", can_change_orders=False)
        with pytest.raises(PermissionDenied):
            _event_for(_request(team), "org-a", "some-event", "can_change_orders")

    def test_unknown_event_is_a_404(self):
        team = _team("org-a")
        with patch("pretix_autoconfig.api_private_links.Event") as ev:
            ev.DoesNotExist = Exception
            ev.objects.get.side_effect = ev.DoesNotExist
            with pytest.raises((NotFound, Exception)):
                _event_for(_request(team), "org-a", "nope", "can_view_orders")

    def test_event_outside_a_limited_team_is_refused(self):
        team = _team("org-a", all_events=False)
        event = SimpleNamespace(pk=1)
        with patch("pretix_autoconfig.api_private_links.Event") as ev:
            ev.objects.get.return_value = event
            with pytest.raises(PermissionDenied):
                _event_for(_request(team), "org-a", "some-event", "can_view_orders")


class TestBlastRadius:
    def test_release_is_scoped_to_one_named_voucher(self):
        release = SOURCE.split("def release(", 1)[1]
        assert "voucher=voucher" in release
        # Never a bulk delete across the event or across private links in general.
        assert "tag__startswith=PRIVATE_TAG_PREFIX).delete()" not in release
        assert release.count(".delete()") == 1

    def test_only_private_link_vouchers_are_visible(self):
        assert 'PRIVATE_TAG_PREFIX = "private:"' in SOURCE
        assert "tag__startswith=PRIVATE_TAG_PREFIX" in SOURCE

    def test_reads_need_view_permission_and_writes_need_change(self):
        assert '"can_view_orders"' in SOURCE
        assert '"can_change_orders"' in SOURCE

    def test_holds_ignore_expired_reservations(self):
        # An expired-but-not-yet-swept position is not a hold; reporting it would have the
        # organizer releasing a reservation that already ended.
        assert "expires__gte=now()" in SOURCE
