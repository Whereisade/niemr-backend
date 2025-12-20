# notifications/management/commands/process_reminders.py
"""
Management command to process and send due reminders.

Usage:
    python manage.py process_reminders                # Process all due reminders
    python manage.py process_reminders --dry-run     # Show what would be processed
    python manage.py process_reminders --lookahead 15 # Include reminders due within 15 minutes

This command should be run on a schedule (e.g., every 5 minutes via cron/celery beat).
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta


class Command(BaseCommand):
    help = "Process and send due reminders"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be processed without actually doing it",
        )
        parser.add_argument(
            "--lookahead",
            type=int,
            default=5,
            help="Process reminders due within this many minutes (default: 5)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Process in batches of this size (default: 100)",
        )

    def handle(self, *args, **options):
        from notifications.models import Reminder
        from notifications.services import notify_user
        from notifications.enums import Topic, Priority

        dry_run = options["dry_run"]
        lookahead = options["lookahead"]
        batch_size = options["batch_size"]

        now = timezone.now()
        cutoff = now + timedelta(minutes=lookahead)

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes will be made"))

        # Find pending reminders that are due
        due_reminders = Reminder.objects.filter(
            status="PENDING",
            reminder_time__lte=cutoff,
        ).select_related("patient", "nurse", "facility")[:batch_size]

        due_count = due_reminders.count()

        if due_count == 0:
            self.stdout.write("No reminders due at this time")
            return

        self.stdout.write(f"Found {due_count} reminders due for processing")

        sent_count = 0
        failed_count = 0

        for reminder in due_reminders:
            if dry_run:
                self.stdout.write(
                    f"Would send: [{reminder.reminder_type}] "
                    f"Patient: {reminder.patient} - {reminder.message[:50]}..."
                )
                continue

            try:
                # Determine the recipient (nurse assigned to the reminder)
                if not reminder.nurse or not reminder.nurse.user:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Reminder {reminder.id} has no nurse with user account, skipping"
                        )
                    )
                    continue

                # Map reminder type to notification topic
                topic_map = {
                    "VITALS": Topic.REMINDER,
                    "MEDICATION": Topic.PRESCRIPTION_READY,
                    "LAB": Topic.LAB_RESULT_READY,
                    "APPOINTMENT": Topic.APPOINTMENT_REMINDER,
                    "FOLLOW_UP": Topic.REMINDER,
                    "OTHER": Topic.REMINDER,
                }
                topic = topic_map.get(reminder.reminder_type, Topic.REMINDER)

                # Send notification to the nurse
                notify_user(
                    user=reminder.nurse.user,
                    topic=topic,
                    title=f"Reminder: {reminder.get_reminder_type_display()}",
                    body=f"Patient: {reminder.patient.first_name} {reminder.patient.last_name}\n{reminder.message}",
                    facility=reminder.facility,
                    data={
                        "reminder_id": reminder.id,
                        "patient_id": reminder.patient.id,
                        "reminder_type": reminder.reminder_type,
                    },
                    action_url=f"/facility/patients/{reminder.patient.id}/",
                )

                # Update reminder status
                reminder.status = "SENT"
                reminder.sent_at = now
                reminder.save(update_fields=["status", "sent_at", "updated_at"])

                sent_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"Sent reminder {reminder.id} to {reminder.nurse.user.email}")
                )

                # Handle recurring reminders
                if reminder.is_recurring and reminder.recurrence_interval:
                    # Check if we should create another reminder
                    next_time = reminder.reminder_time + reminder.recurrence_interval
                    
                    # Only create if before recurrence end date (if set)
                    if reminder.recurrence_end is None or next_time <= reminder.recurrence_end:
                        Reminder.objects.create(
                            patient=reminder.patient,
                            nurse=reminder.nurse,
                            facility=reminder.facility,
                            reminder_type=reminder.reminder_type,
                            message=reminder.message,
                            reminder_time=next_time,
                            is_recurring=True,
                            recurrence_interval=reminder.recurrence_interval,
                            recurrence_end=reminder.recurrence_end,
                            status="PENDING",
                        )
                        self.stdout.write(
                            f"  Created next recurring reminder for {next_time}"
                        )

            except Exception as e:
                failed_count += 1
                self.stdout.write(
                    self.style.ERROR(f"Failed to process reminder {reminder.id}: {str(e)}")
                )

        # Mark overdue reminders as expired
        overdue_cutoff = now - timedelta(hours=24)
        expired_count = Reminder.objects.filter(
            status="PENDING",
            reminder_time__lt=overdue_cutoff,
        ).update(status="EXPIRED")

        if expired_count > 0:
            self.stdout.write(
                self.style.WARNING(f"Marked {expired_count} overdue reminders as expired")
            )

        # Summary
        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"\nDRY RUN COMPLETE - Would have processed {due_count} reminders")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nProcessing complete: {sent_count} sent, {failed_count} failed"
                )
            )