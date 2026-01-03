from django.core.management.base import BaseCommand
from django.utils import timezone
from emails.models import Outbox, EmailStatus
from emails.services.router import _attempt_send

class Command(BaseCommand):
    help = "Send/retry queued emails via configured provider (SMTP/Resend)"

    def handle(self, *args, **opts):
        now = timezone.now()
        qs = Outbox.objects.filter(status=EmailStatus.QUEUED, next_attempt_at__lte=now).order_by("created_at")[:200]
        if not qs.exists():
            self.stdout.write("Outbox empty")
            return
        for o in qs:
            _attempt_send(o, queue_if_failed=(o.retry_count < 6))
        self.stdout.write(f"Processed {qs.count()} outbox item(s)")
