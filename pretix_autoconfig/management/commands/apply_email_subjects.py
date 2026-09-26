from django.core.management.base import BaseCommand
from django_scopes import scopes_disabled
from pretix.base.models import Event

from pretix_autoconfig.signals_config import _EMAIL_BODY, _EMAIL_SUBJECT


class Command(BaseCommand):
    help = "Apply the configured email subject and body overrides to all existing events."

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

        for event in Event.objects.all().select_related("organizer"):
            current_subject = str(event.settings.get("mail_subject_order_paid") or "")
            current_body = str(event.settings.get("mail_text_order_paid") or "")
            already_correct = current_subject == _EMAIL_SUBJECT and current_body == _EMAIL_BODY

            if already_correct:
                skipped += 1
                continue

            slug = f"{event.organizer.slug}/{event.slug}"
            if dry_run:
                self.stdout.write(f"[dry-run] would update {slug!r}")
            else:
                event.settings.set("mail_subject_order_paid", _EMAIL_SUBJECT)
                event.settings.set("mail_subject_order_free", _EMAIL_SUBJECT)
                event.settings.set("mail_text_order_paid", _EMAIL_BODY)
                event.settings.set("mail_text_order_free", _EMAIL_BODY)
                self.stdout.write(f"Updated {slug!r}")
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{'Would update' if dry_run else 'Updated'} {updated} event(s), {skipped} already correct."
            )
        )
