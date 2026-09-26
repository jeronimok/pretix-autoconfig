"""Configurable branding for PDF tickets.

pretix's default ticket layout ends with a ``poweredby`` element that renders
the pretix logo at the page footer. An installation that sells the ticket under
its own name can supply a background PDF instead, and may opt into dropping that
element.

Both are configuration, and both default to off:

``ticket_background``    path to a PDF drawn behind every ticket
``strip_pretix_logo``    remove pretix's ``poweredby`` element

With neither set, the plugin does not touch ticket layouts at all.

Removing the element is permitted -- pretix's LICENSE requires the attribution
notice on "generated web pages", and a downloaded PDF is not one -- but it is
the installer's call, so it is opt-in rather than assumed.

The layout is derived from pretix's own ``DEFAULT_TICKET_LAYOUT`` at call time
rather than copied, so element positions never drift from the installed pretix
version. A background is drawn against fixed coordinates though, so
tests/test_ticket_layout.py asserts those positions still line up -- a pretix
upgrade that moves an element fails loudly instead of shipping a crooked ticket.
"""

import json
import logging

from django.core.files.base import ContentFile
from django.utils.translation import gettext_noop
from pretix.plugins.ticketoutputpdf.models import DEFAULT_TICKET_LAYOUT

logger = logging.getLogger(__name__)

# Bump to re-apply the branding to every event on the next backfill run, e.g.
# after redesigning the background.
LAYOUT_VERSION = 1
VERSION_SETTING = "autoconfig_ticket_layout_version"

# pretix names its auto-created layout this; keep it so the control panel reads
# the same as an untouched install.
DEFAULT_LAYOUT_NAME = gettext_noop("Default layout")


def branded_layout():
    """pretix's default ticket layout, minus the "powered by pretix" element when configured."""
    # Imported lazily: signals_config imports this module, so a module-level import would be
    # circular.
    from pretix_autoconfig.signals_config import get_strip_pretix_logo

    layout = json.loads(DEFAULT_TICKET_LAYOUT)
    if not get_strip_pretix_logo():
        return layout
    return [o for o in layout if o.get("type") != "poweredby"]


def _is_untouched(layout):
    """True if the organizer has not edited this layout themselves.

    A layout counts as untouched when its elements are still either pretix's
    default or our branded variant. Anything else is the organizer's own work
    and is left alone unless the caller forces the issue.
    """
    try:
        current = json.loads(layout.layout or "[]")
    except ValueError:
        return False
    return current in (json.loads(DEFAULT_TICKET_LAYOUT), branded_layout())


def _read_background():
    """Bytes of the configured background PDF, or None when none is configured."""
    from pretix_autoconfig.signals_config import get_ticket_background_path

    path = get_ticket_background_path()
    if not path:
        return None
    with open(path, "rb") as f:
        return f.read()


def apply_branded_layout(event, force=False):
    """Apply the configured branding to an event's default ticket layout.

    Returns True if the layout was written, False if it was left alone. Skips
    when nothing is configured, when the event is already on the current
    LAYOUT_VERSION, or when the organizer has customized the layout -- pass
    force=True to override the last of those.
    """
    from pretix_autoconfig.signals_config import get_strip_pretix_logo, get_ticket_background_path

    background_path = get_ticket_background_path()
    if not background_path and not get_strip_pretix_logo():
        # Nothing configured: leave the organizer's ticket layouts alone.
        return False

    if not force and event.settings.get(VERSION_SETTING, as_type=int) == LAYOUT_VERSION:
        return False

    layout, created = event.ticket_layouts.get_or_create(
        default=True,
        defaults={"name": DEFAULT_LAYOUT_NAME},
    )

    if not created and not force and not _is_untouched(layout):
        logger.info(
            "Skipping ticket layout for %s/%s: customized by the organizer",
            event.organizer.slug,
            event.slug,
        )
        return False

    try:
        background = _read_background()
    except OSError:
        logger.exception(
            "Ticket background %s could not be read for %s/%s; leaving the layout alone",
            background_path,
            event.organizer.slug,
            event.slug,
        )
        return False

    layout.layout = json.dumps(branded_layout())
    if background is not None:
        # save=False so the FileField write and the layout write land in one UPDATE.
        layout.background.save("background.pdf", ContentFile(background), save=False)
    layout.save()

    event.settings.set(VERSION_SETTING, LAYOUT_VERSION)
    return True


def has_branded_layout(event):
    """True if this event is already on the current branding version."""
    return event.settings.get(VERSION_SETTING, as_type=int) == LAYOUT_VERSION


def is_customized(event):
    """True if the organizer has edited this event's default ticket layout.

    Lets callers report why an event would be skipped without writing to it.
    """
    layout = event.ticket_layouts.filter(default=True).first()
    return layout is not None and not _is_untouched(layout)


__all__ = [
    "LAYOUT_VERSION",
    "apply_branded_layout",
    "branded_layout",
    "has_branded_layout",
    "is_customized",
]
