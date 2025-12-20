# notifications/management/commands/cleanup_notifications.py
"""
Management command to clean up expired and old notifications.

Usage:
    python manage.py cleanup_notifications                  # Default: delete expired, archive 90+ days old
    python manage.py cleanup_notifications --dry-run        # Show what would be deleted
    python manage.py cleanup_notifications --days 30        # Archive notifications older than 30 days
    python manage.py cleanup_notifications --delete-archived 180  # Delete archived older than 180 days
    python manage.py cleanup_notifications --expired-only   # Only delete expired notifications
"""

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta


class Command(BaseCommand):
    help = "Clean up expired and old notifications"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be cleaned up without actually doing it",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Archive read notifications older than this many days (default: 90)",
        )
        parser.add_argument(
            "--delete-archived",
            type=int,
            default=None,
            help="Delete archived notifications older than this many days",
        )
        parser.add_argument(
            "--expired-only",
            action="store_true",
            help="Only delete expired notifications, don't archive old ones",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Process in batches of this size (default: 1000)",
        )

    def handle(self, *args, **options):
        from notifications.models import Notification

        dry_run = options["dry_run"]
        days = options["days"]
        delete_archived_days = options["delete_archived"]
        expired_only = options["expired_only"]
        batch_size = options["batch_size"]

        now = timezone.now()
        total_deleted = 0
        total_archived = 0

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes will be made"))

        # 1. Delete expired notifications
        expired_qs = Notification.objects.filter(
            expires_at__isnull=False,
            expires_at__lt=now,
        )
        expired_count = expired_qs.count()

        if expired_count > 0:
            if dry_run:
                self.stdout.write(f"Would delete {expired_count} expired notifications")
            else:
                # Delete in batches to avoid memory issues
                while True:
                    batch_ids = list(expired_qs.values_list("id", flat=True)[:batch_size])
                    if not batch_ids:
                        break
                    deleted, _ = Notification.objects.filter(id__in=batch_ids).delete()
                    total_deleted += deleted
                    self.stdout.write(f"Deleted {deleted} expired notifications...")

                self.stdout.write(
                    self.style.SUCCESS(f"Deleted {total_deleted} expired notifications")
                )

        if expired_only:
            return

        # 2. Archive old read notifications
        archive_cutoff = now - timedelta(days=days)
        old_read_qs = Notification.objects.filter(
            is_read=True,
            is_archived=False,
            created_at__lt=archive_cutoff,
        )
        old_read_count = old_read_qs.count()

        if old_read_count > 0:
            if dry_run:
                self.stdout.write(
                    f"Would archive {old_read_count} read notifications older than {days} days"
                )
            else:
                # Archive in batches
                while True:
                    batch_ids = list(old_read_qs.values_list("id", flat=True)[:batch_size])
                    if not batch_ids:
                        break
                    updated = Notification.objects.filter(id__in=batch_ids).update(
                        is_archived=True,
                        archived_at=now,
                    )
                    total_archived += updated
                    self.stdout.write(f"Archived {updated} old notifications...")

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Archived {total_archived} notifications older than {days} days"
                    )
                )

        # 3. Delete old archived notifications (if requested)
        if delete_archived_days:
            delete_cutoff = now - timedelta(days=delete_archived_days)
            old_archived_qs = Notification.objects.filter(
                Q(archived_at__lt=delete_cutoff) | Q(
                    archived_at__isnull=True,
                    created_at__lt=delete_cutoff,
                ),
                is_archived=True,
            )
            old_archived_count = old_archived_qs.count()

            if old_archived_count > 0:
                if dry_run:
                    self.stdout.write(
                        f"Would delete {old_archived_count} archived notifications "
                        f"older than {delete_archived_days} days"
                    )
                else:
                    deleted_archived = 0
                    while True:
                        batch_ids = list(
                            old_archived_qs.values_list("id", flat=True)[:batch_size]
                        )
                        if not batch_ids:
                            break
                        deleted, _ = Notification.objects.filter(id__in=batch_ids).delete()
                        deleted_archived += deleted
                        self.stdout.write(f"Deleted {deleted} archived notifications...")

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Deleted {deleted_archived} archived notifications "
                            f"older than {delete_archived_days} days"
                        )
                    )
                    total_deleted += deleted_archived

        # Summary
        if dry_run:
            self.stdout.write(self.style.WARNING("\nDRY RUN COMPLETE - No changes made"))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nCleanup complete: {total_deleted} deleted, {total_archived} archived"
                )
            )