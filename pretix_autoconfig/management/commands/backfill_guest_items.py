from django.core.management.base import BaseCommand
from django_scopes import scopes_disabled
from pretix.base.models import Event

from pretix_autoconfig.signals_config import _configure_guest_item_on_event_created, get_guest_item_internal_name


class Command(BaseCommand):
    help = (
        "Create the hidden 'Guest' item on all existing events. New events get one "
        "automatically on creation; this backfills events created before that."
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
        created = 0
        skipped = 0
        failed = 0
        internal_name = get_guest_item_internal_name()

        for event in Event.objects.all().select_related("organizer"):
            slug = f"{event.organizer.slug}/{event.slug}"
            if event.items.filter(internal_name=internal_name).exists():
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(f"[dry-run] would create guest item for {slug!r}")
                created += 1
                continue

            try:
                _configure_guest_item_on_event_created(event)
            except Exception as e:
                self.stderr.write(f"FAILED {slug!r}: {e}")
                failed += 1
                continue

            self.stdout.write(f"Created guest item for {slug!r}")
            created += 1

        summary = f"{'Would create' if dry_run else 'Created'} {created} guest item(s), {skipped} already had one."
        if failed:
            self.stderr.write(f"{summary} {failed} failed.")
        else:
            self.stdout.write(self.style.SUCCESS(summary))
