from django.conf import settings
from django.utils import timezone

from emails.models import Outbox, EmailStatus
from emails.services.render import render_template

from .providers.resend_provider import send_via_resend
from .providers.smtp_provider import send_via_smtp

def send_email(
    *,
    to: str,
    subject: str = "",
    html: str = "",
    text: str = "",
    tags=None,
    template_code: str | None = None,
    template_data: dict | None = None,
    from_email: str | None = None,
    cc=None,
    bcc=None,
    reply_to=None,
    attachment_file_ids=None,
    queue_if_failed: bool = True,
) -> int:
    """Single entry point for all system emails.

    Provider is selected via settings.EMAILS_PROVIDER:
    - SMTP (default): Django SMTP backend (supports Google SMTP)
    - RESEND: Resend REST API
    """
    if template_code:
        sub, h, t = render_template(template_code, template_data or {})
        subject = subject or sub
        html = html or h
        text = text or t

    o = Outbox.objects.create(
        to=to, subject=subject, html=html, text=text,
        from_email=from_email or "",
        cc=cc or [], bcc=bcc or [], reply_to=reply_to or [],
        tags=tags or [],
        template_code=template_code or "",
        template_data=template_data or {},
        attachment_file_ids=attachment_file_ids or [],
        status=EmailStatus.QUEUED,
    )

    _attempt_send(o, queue_if_failed=queue_if_failed)
    return o.id

def _attempt_send(outbox: Outbox, *, queue_if_failed: bool):
    outbox.status = EmailStatus.SENDING
    outbox.save(update_fields=["status"])

    provider = (getattr(settings, "EMAILS_PROVIDER", "SMTP") or "SMTP").upper()

    if provider == "RESEND":
        mid, err = send_via_resend(outbox=outbox)
    else:
        # Default to SMTP
        mid, err = send_via_smtp(outbox=outbox)

    if err:
        outbox.status = EmailStatus.FAILED if not queue_if_failed else EmailStatus.QUEUED
        outbox.retry_count += 1
        backoff = getattr(settings, "EMAILS_RETRY_BACKOFF_SEC", 120)
        outbox.next_attempt_at = timezone.now() + timezone.timedelta(
            seconds=backoff * max(1, 2 ** (outbox.retry_count - 1))
        )
        outbox.last_error = err[:2000]
        outbox.save(update_fields=["status","retry_count","next_attempt_at","last_error"])
        return

    outbox.provider_message_id = mid or ""
    outbox.status = EmailStatus.SENT
    outbox.sent_at = timezone.now()
    outbox.last_error = ""
    outbox.save(update_fields=["provider_message_id","status","sent_at","last_error"])
