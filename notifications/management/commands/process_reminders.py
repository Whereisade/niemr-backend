"""notifications.management.commands.process_reminders

Processes due reminders and turns them into in-app notifications for the assigned nurse.

Run periodically (cron / Celery beat), e.g. every 1-5 minutes.

Usage:
  python manage.py process_reminders
  python manage.py process_reminders --dry-run
  python manage.py process_reminders --lookahead 5
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Process and send due reminders"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Do not send, just print")
        parser.add_argument("--lookahead", type=int, default=5, help="Minutes to look ahead")
        parser.add_argument("--batch-size", type=int, default=200, help="Max reminders per run")

    def handle(self, *args, **options):
        from notifications.models import Reminder
        from notifications.services.notify import notify_user
        from notifications.enums import Topic, Priority

        dry_run = bool(options["dry_run"])
        lookahead = int(options["lookahead"])
        batch_size = int(options["batch_size"])

        now = timezone.now()
        cutoff = now + timedelta(minutes=lookahead)

        qs = (
            Reminder.objects.select_related("patient", "nurse")
            .filter(status=Reminder.Status.PENDING, reminder_time__lte=cutoff)
            .order_by("reminder_time")
        )

        due = list(qs[:batch_size])
        if not due:
            self.stdout.write("No reminders due")
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no notifications will be created"))

        sent = 0
        skipped = 0
        failed = 0

        for rem in due:
            if not rem.nurse_id:
                skipped += 1
                continue

            title = f"Reminder: {rem.get_reminder_type_display()}"
            patient_name = getattr(rem.patient, "full_name", None) or f"Patient #{rem.patient_id}"
            body = f"{patient_name}\n{rem.message}"

            if dry_run:
                self.stdout.write(f"Would notify nurse={rem.nurse_id} reminder={rem.id} time={rem.reminder_time}")
                continue

            try:
                notify_user(
                    user=rem.nurse,
                    topic=Topic.REMINDER,
                    priority=Priority.NORMAL,
                    title=title,
                    body=body,
                    facility_id=getattr(getattr(rem.patient, "facility", None), "id", None),
                    data={"reminder_id": rem.id, "patient_id": rem.patient_id, "reminder_type": rem.reminder_type},
                    action_url=f"/facility/patients/{rem.patient_id}",
                    group_key=f"REMINDER:{rem.id}",
                )

                rem.status = Reminder.Status.SENT
                rem.sent_at = now
                rem.save(update_fields=["status", "sent_at"])

                sent += 1
            except Exception as e:
                failed += 1
                self.stdout.write(self.style.ERROR(f"Failed reminder {rem.id}: {e}"))

        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN complete. Would process: {len(due)}"))
            return

        self.stdout.write(self.style.SUCCESS(f"Processed reminders: sent={sent} skipped={skipped} failed={failed}"))
