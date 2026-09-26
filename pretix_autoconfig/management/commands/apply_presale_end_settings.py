from django.core.management.base import BaseCommand
from django_scopes import scopes_disabled
from pretix.base.models import Event

from pretix_autoconfig.signals_config import _PRESALE_HAS_ENDED_TEXT


class Command(BaseCommand):
    help = (
        "Apply the presale-end webshop-close settings (show_items_outside_presale_period, "
        "presale_has_ended_text) to all existing events. New events get these automatically "
        "on creation; this backfills events created before that."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print which events would be updated without changing anything.",
        )

    @scopes_disabled()
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        updated = 0
        skipped = 0
        failed = 0

        for event in Event.objects.all().select_related("organizer"):
            slug = f"{event.organizer.slug}/{event.slug}"
            try:
                already_correct = (
                    event.settings.get("show_items_outside_presale_period", as_type=bool) is False
                    and str(event.settings.get("presale_has_ended_text") or "") == _PRESALE_HAS_ENDED_TEXT
                )
            except Exception as e:
                self.stderr.write(f"FAILED {slug!r}: {e}")
                failed += 1
                continue

            if already_correct:
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(f"[dry-run] would update {slug!r}")
                updated += 1
                continue

            try:
                event.settings.set("show_items_outside_presale_period", False)
                event.settings.set("presale_has_ended_text", _PRESALE_HAS_ENDED_TEXT)
            except Exception as e:
                self.stderr.write(f"FAILED {slug!r}: {e}")
                failed += 1
                continue

            self.stdout.write(f"Updated {slug!r}")
            updated += 1

        summary = f"{'Would update' if dry_run else 'Updated'} {updated} event(s), {skipped} already correct."
        if failed:
            self.stderr.write(f"{summary} {failed} failed.")
        else:
            self.stdout.write(self.style.SUCCESS(summary))
