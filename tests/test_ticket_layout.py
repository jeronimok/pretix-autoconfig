import json

import pytest
from django_scopes import scopes_disabled
from pretix.base.models import Event, Organizer
from pretix.plugins.ticketoutputpdf.models import DEFAULT_TICKET_LAYOUT

from pretix_autoconfig import ticket_layout
from pretix_autoconfig.ticket_layout import (
    LAYOUT_VERSION,
    apply_branded_layout,
    branded_layout,
    has_branded_layout,
    is_customized,
)


@pytest.fixture
def background_pdf(tmp_path):
    """A blank A4 PDF standing in for whatever artwork an installation supplies.

    Generated rather than committed: the plugin ships no artwork of its own any more, and a
    fixture file would be the first step back towards shipping some.
    """
    from pypdf import PdfWriter

    path = tmp_path / "background_a4.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595.276, height=841.89)  # A4 in points
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


@pytest.fixture
def branding_configured(monkeypatch, background_pdf):
    """Both keys set, i.e. how a platform like the one deploying this plugin configures it."""
    from pretix_autoconfig import signals_config

    monkeypatch.setattr(signals_config, "get_ticket_background_path", lambda: str(background_pdf))
    monkeypatch.setattr(signals_config, "get_strip_pretix_logo", lambda: True)
    return background_pdf


@pytest.fixture
def branding_unconfigured(monkeypatch):
    from pretix_autoconfig import signals_config

    monkeypatch.setattr(signals_config, "get_ticket_background_path", lambda: None)
    monkeypatch.setattr(signals_config, "get_strip_pretix_logo", lambda: False)


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


class TestBrandedLayout:
    def test_drops_only_the_poweredby_element(self, branding_configured):
        default = json.loads(DEFAULT_TICKET_LAYOUT)
        branded = branded_layout()

        assert any(o.get("type") == "poweredby" for o in default), (
            "pretix's default layout no longer has a poweredby element -- "
            "the branding may now be applied somewhere else entirely"
        )
        assert not any(o.get("type") == "poweredby" for o in branded)
        assert branded == [o for o in default if o.get("type") != "poweredby"]

    def test_keeps_poweredby_when_not_configured(self, branding_unconfigured):
        """A default install must not silently strip upstream attribution."""
        assert branded_layout() == json.loads(DEFAULT_TICKET_LAYOUT)

    def test_reads_the_configured_background(self, branding_configured):
        assert ticket_layout._read_background().startswith(b"%PDF")

    def test_no_background_configured_reads_nothing(self, branding_unconfigured):
        assert ticket_layout._read_background() is None


class TestGeometryDriftGuard:
    """The background draws rules at fixed coordinates.

    A background PDF is drawn against fixed coordinates, so if a pretix upgrade
    moves an element the artwork would cut through text. These are pretix's own
    default-layout positions -- if this fails, whoever supplies a background
    needs to re-check the design and regenerate it.
    """

    EXPECTED_BOTTOMS = {
        "event_name": 272.09,
        "itemvar": 261.77,
        "attendee_name": 251.30,
        "event_location": 203.43,
        "order": 193.33,
    }

    def test_text_element_positions_unchanged(self):
        by_content = {o.get("content"): o for o in branded_layout() if o.get("type") == "textcontainer"}
        for content, bottom in self.EXPECTED_BOTTOMS.items():
            assert content in by_content, f"layout no longer has a {content!r} element"
            assert float(by_content[content]["bottom"]) == pytest.approx(bottom, abs=0.01), (
                f"{content!r} moved; regenerate the ticket background"
            )

    def test_barcode_position_unchanged(self):
        barcode = next(o for o in branded_layout() if o.get("type") == "barcodearea")
        assert float(barcode["left"]) == pytest.approx(130.40, abs=0.01)
        assert float(barcode["bottom"]) == pytest.approx(204.50, abs=0.01)
        assert float(barcode["size"]) == pytest.approx(64.00, abs=0.01)


@pytest.mark.django_db
class TestApplyOnEventCreation:
    def test_new_event_gets_a_branded_layout(self, branding_configured, event):
        layout = event.ticket_layouts.filter(default=True).first()

        assert layout is not None
        assert json.loads(layout.layout) == branded_layout()
        assert layout.background.name

    def test_new_event_is_stamped_with_the_layout_version(self, branding_configured, event):
        assert has_branded_layout(event) is True
        assert event.settings.get("autoconfig_ticket_layout_version", as_type=int) == LAYOUT_VERSION

    def test_event_creation_survives_a_branding_failure(self, branding_configured, organizer, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("no background for you")

        monkeypatch.setattr("pretix_autoconfig.signals_config.apply_branded_layout", boom)

        # An unbranded ticket is recoverable; a half-created event is not.
        ev = Event.objects.create(
            organizer=organizer,
            name="Resilient",
            slug="resilient",
            date_from="2026-06-01T18:00:00Z",
            plugins="pretix_autoconfig",
        )

        with scopes_disabled():
            assert Event.objects.filter(pk=ev.pk).exists()
        assert has_branded_layout(ev) is False


@pytest.mark.django_db
class TestApplyBrandedLayout:
    def test_is_idempotent(self, branding_configured, event):
        assert apply_branded_layout(event) is False, "already applied on creation"

    def test_reapplies_when_the_version_is_bumped(self, branding_configured, event, monkeypatch):
        monkeypatch.setattr(ticket_layout, "LAYOUT_VERSION", LAYOUT_VERSION + 1)

        assert apply_branded_layout(event) is True

    def test_skips_a_layout_the_organizer_customized(self, branding_configured, event, monkeypatch):
        layout = event.ticket_layouts.get(default=True)
        custom = branded_layout() + [{"type": "textarea", "content": "Bring ID", "page": 1}]
        layout.layout = json.dumps(custom)
        layout.save()
        # Force re-evaluation past the version short-circuit.
        monkeypatch.setattr(ticket_layout, "LAYOUT_VERSION", LAYOUT_VERSION + 1)

        assert is_customized(event) is True
        assert apply_branded_layout(event) is False
        layout.refresh_from_db()
        assert json.loads(layout.layout) == custom

    def test_force_overwrites_a_customized_layout(self, branding_configured, event):
        layout = event.ticket_layouts.get(default=True)
        layout.layout = json.dumps([{"type": "textarea", "content": "Bring ID", "page": 1}])
        layout.save()

        assert apply_branded_layout(event, force=True) is True
        layout.refresh_from_db()
        assert json.loads(layout.layout) == branded_layout()

    def test_pretix_default_layout_counts_as_untouched(self, branding_configured, event, monkeypatch):
        layout = event.ticket_layouts.get(default=True)
        layout.layout = DEFAULT_TICKET_LAYOUT
        layout.save()
        monkeypatch.setattr(ticket_layout, "LAYOUT_VERSION", LAYOUT_VERSION + 1)

        assert is_customized(event) is False
        assert apply_branded_layout(event) is True


@pytest.mark.django_db
class TestUnconfigured:
    """With neither key set the plugin must leave ticket layouts alone entirely."""

    def test_event_creation_writes_no_layout(self, branding_unconfigured, organizer):
        with scopes_disabled():
            event = Event.objects.create(
                organizer=organizer,
                name="Plain Event",
                slug="plain-event",
                date_from="2026-06-01T18:00:00Z",
                plugins="pretix_autoconfig",
            )
        assert event.ticket_layouts.count() == 0
        assert not has_branded_layout(event)

    def test_apply_returns_false(self, branding_unconfigured, event):
        assert apply_branded_layout(event, force=True) is False


@pytest.mark.django_db
class TestUnreadableBackground:
    """A configured path that cannot be read must be loud, not silent.

    Event creation deliberately survives a branding failure, so without a logged exception a
    typo in the path would look exactly like a working install that quietly stops branding --
    the failure mode that cost six weeks with the VAT rates.
    """

    @pytest.fixture
    def missing_background(self, monkeypatch, tmp_path):
        from pretix_autoconfig import signals_config

        monkeypatch.setattr(signals_config, "get_ticket_background_path", lambda: str(tmp_path / "nope.pdf"))
        monkeypatch.setattr(signals_config, "get_strip_pretix_logo", lambda: True)

    def test_logs_an_exception_and_leaves_the_layout_alone(self, missing_background, event, caplog):
        with caplog.at_level("ERROR", logger="pretix_autoconfig.ticket_layout"):
            written = apply_branded_layout(event, force=True)

        assert written is False
        assert any("could not be read" in r.message for r in caplog.records)
        assert any(r.exc_info for r in caplog.records), "expected logger.exception, not logger.error"

    def test_event_is_not_stamped_as_branded(self, missing_background, event):
        apply_branded_layout(event, force=True)
        assert not has_branded_layout(event)
