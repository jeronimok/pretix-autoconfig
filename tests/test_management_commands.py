"""Both backfill commands iterate every event across organizers.

pretix scopes Event queries by organizer via django-scopes, so a command that
forgets scopes_disabled() raises ScopeError the moment it runs -- and neither
command has a code path that would catch it. These tests just run them.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django_scopes import scopes_disabled
from pretix.base.models import Event, Organizer


@pytest.fixture
def organizer(db):
    return Organizer.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def event(organizer):
    return Event.objects.create(
        organizer=organizer,
        name="Test Event",
        slug="test-event",
        date_from="2026-06-01T18:00:00Z",
        plugins="pretix_autoconfig",
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "command",
    ["apply_email_subjects", "apply_ticket_layouts", "apply_presale_end_settings", "backfill_guest_items"],
)
def test_dry_run_iterates_events_without_a_scope(command, event):
    out = StringIO()

    # Raises ScopeError if the command forgets scopes_disabled().
    call_command(command, "--dry-run", stdout=out, stderr=out)

    assert out.getvalue().strip(), "command produced no summary"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "command",
    ["apply_email_subjects", "apply_ticket_layouts", "apply_presale_end_settings", "backfill_guest_items"],
)
def test_runs_without_a_scope(command, event):
    out = StringIO()

    call_command(command, stdout=out, stderr=out)

    assert out.getvalue().strip(), "command produced no summary"


@pytest.mark.django_db
class TestApplyPresaleEndSettings:
    """Backfills events created before the webshop-close settings existed."""

    def test_backfills_an_event_created_before_the_feature(self, event):
        # The creation signal already applied the correct settings -- reset to what a
        # pre-existing event actually looks like before running the real backfill.
        event.settings.set("show_items_outside_presale_period", True)
        event.settings.set("presale_has_ended_text", "")

        call_command("apply_presale_end_settings", stdout=StringIO(), stderr=StringIO())

        # The command loads its own Event instances -- hierarkey's settings cache is
        # per-instance, so re-fetch rather than trust the fixture's stale cache.
        with scopes_disabled():
            refreshed = Event.objects.get(pk=event.pk)
        assert refreshed.settings.get("show_items_outside_presale_period", as_type=bool) is False
        assert str(refreshed.settings.get("presale_has_ended_text")) == (
            "This event has ended. Tickets are no longer available."
        )

    def test_already_correct_event_is_skipped(self, event):
        out = StringIO()

        call_command("apply_presale_end_settings", stdout=out, stderr=out)

        assert "0 event(s), 1 already correct" in out.getvalue()

    def test_dry_run_does_not_write(self, event):
        event.settings.set("show_items_outside_presale_period", True)

        call_command("apply_presale_end_settings", "--dry-run", stdout=StringIO(), stderr=StringIO())

        assert event.settings.get("show_items_outside_presale_period", as_type=bool) is True


@pytest.mark.django_db
class TestBackfillGuestItems:
    """Backfills events created before the guest item existed."""

    # Item queries are organizer-scoped by django-scopes; the command itself runs inside
    # scopes_disabled(), but reading its result back from the test needs its own scope too.
    @pytest.fixture(autouse=True)
    def _scopes_disabled(self):
        with scopes_disabled():
            yield

    def test_backfills_an_event_missing_the_item(self, event):
        # The creation signal already added one -- reset to what a pre-existing event
        # actually looks like before running the real backfill.
        event.items.filter(internal_name="guest").delete()

        call_command("backfill_guest_items", stdout=StringIO(), stderr=StringIO())

        assert event.items.filter(internal_name="guest").exists()

    def test_already_correct_event_is_skipped(self, event):
        out = StringIO()

        call_command("backfill_guest_items", stdout=out, stderr=out)

        assert "0 guest item(s), 1 already had one" in out.getvalue()

    def test_dry_run_does_not_write(self, event):
        event.items.filter(internal_name="guest").delete()

        call_command("backfill_guest_items", "--dry-run", stdout=StringIO(), stderr=StringIO())

        assert not event.items.filter(internal_name="guest").exists()
