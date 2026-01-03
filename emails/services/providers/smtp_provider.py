"""emails/services/providers/smtp_provider.py

SMTP provider for the NIEMR emails app.

This uses Django's configured email backend (EMAIL_BACKEND / EMAIL_HOST / ...)
and therefore supports Google SMTP (Gmail / Google Workspace) out of the box.

Notes
-----
* This provider supports HTML + text bodies.
* It supports optional attachments referenced by `attachment_file_ids`.
"""

from __future__ import annotations

from email.utils import make_msgid

from django.apps import apps
from django.conf import settings
from django.core.mail import EmailMultiAlternatives


def _attach_files(msg: EmailMultiAlternatives, file_ids: list[int] | None):
    """Attach uploaded files (attachments.File) by id."""
    if not file_ids:
        return

    try:
        File = apps.get_model("attachments", "File")
    except Exception:
        return

    qs = File.objects.filter(id__in=file_ids)
    for f in qs:
        try:
            fp = getattr(f, "file", None)
            if not fp:
                continue
            name = getattr(f, "original_name", "") or fp.name.rsplit("/", 1)[-1]
            mime = getattr(f, "mime_type", "") or None

            fp.open("rb")
            content = fp.read()
            fp.close()

            # Django will base64-encode when needed.
            msg.attach(name, content, mime)
        except Exception:
            # Never fail the email send just because one attachment couldn't be read.
            continue


def send_via_smtp(*, outbox) -> tuple[str | None, str | None]:
    """Send an Outbox email via SMTP. Returns (message_id, error)."""
    try:
        from_email = (outbox.from_email or "").strip() or getattr(settings, "DEFAULT_FROM_EMAIL", "")
        to = [outbox.to]
        cc = list(outbox.cc or [])
        bcc = list(outbox.bcc or [])
        reply_to = list(outbox.reply_to or []) or None

        # Ensure we always have a plain-text body (some SMTP relays dislike empty text).
        text_body = (outbox.text or "").strip() or " "

        msg = EmailMultiAlternatives(
            subject=outbox.subject,
            body=text_body,
            from_email=from_email,
            to=to,
            cc=cc,
            bcc=bcc,
            reply_to=reply_to,
        )

        if outbox.html:
            msg.attach_alternative(outbox.html, "text/html")

        _attach_files(msg, list(outbox.attachment_file_ids or []))

        # Stable message id to store in outbox.provider_message_id.
        message_id = make_msgid()
        msg.extra_headers = {**(msg.extra_headers or {}), "Message-ID": message_id}

        msg.send(fail_silently=False)
        return message_id, None
    except Exception as e:
        return None, str(e)
