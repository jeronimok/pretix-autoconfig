from django.core.management.base import BaseCommand
from django_scopes import scopes_disabled
from pretix.base.models import Event

from pretix_autoconfig.ticket_layout import (
    LAYOUT_VERSION,
    apply_branded_layout,
    has_branded_layout,
    is_customized,
)


class Command(BaseCommand):
    help = (
        "Apply the configured PDF ticket branding to all existing events. "
        "Events already on the current layout version are skipped, as are layouts "
        "the organizer has customized (use --force to overwrite those too)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print which events would be updated without changing anything.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Also overwrite ticket layouts that the organizer has customized.",
        )

    @scopes_disabled()
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]
        updated = 0
        skipped = 0
        failed = 0

        for event in Event.objects.all().select_related("organizer"):
            slug = f"{event.organizer.slug}/{event.slug}"

            if dry_run:
                # Report what a real run would do, without writing anything.
                if not force and has_branded_layout(event):
                    skipped += 1
                    continue
                if not force and is_customized(event):
                    self.stdout.write(f"[dry-run] would SKIP {slug!r} (layout customized by organizer)")
                    skipped += 1
                    continue
                self.stdout.write(f"[dry-run] would update {slug!r}")
                updated += 1
                continue

            try:
                changed = apply_branded_layout(event, force=force)
            except Exception as e:
                self.stderr.write(f"FAILED {slug!r}: {e}")
                failed += 1
                continue

            if changed:
                self.stdout.write(f"Updated {slug!r}")
                updated += 1
            else:
                skipped += 1

        summary = f"{updated} updated, {skipped} skipped (layout version {LAYOUT_VERSION})"
        if failed:
            self.stderr.write(f"{summary}, {failed} failed")
        else:
            self.stdout.write(summary)
