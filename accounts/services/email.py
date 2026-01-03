"""accounts/services/email.py

Compatibility wrapper used by accounts views.

Historically this project sent auth emails directly via the Resend SDK.
The canonical email pathway is now the `emails` app outbox + provider router
(SMTP / Resend). Keeping this wrapper avoids large refactors in the accounts
module while enabling Google SMTP.
"""

from __future__ import annotations


def send_email(subject: str, to: str, html: str, tags: list[str] | None = None):
    """Queue and attempt to send an email via the configured provider."""
    try:
        from emails.services.router import send_email as router_send_email

        outbox_id = router_send_email(
            to=to,
            subject=subject,
            html=html,
            tags=tags or [],
        )
        return {"outbox_id": outbox_id}
    except Exception as e:
        # Best-effort: don't break auth flows if email sending isn't configured.
        print(f"[EMAIL] Failed to send: {subject} -> {to}: {e}")
        return {"error": str(e)}
