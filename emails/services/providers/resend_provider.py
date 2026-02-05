import json
import urllib.request

from django.conf import settings

def _http_post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    timeout = getattr(settings, "EMAILS_HTTP_TIMEOUT", 10)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")

def send_via_resend(*, outbox) -> tuple[str | None, str | None]:
    """
    Send via Resend REST API. Returns (message_id, error)
    """
    api_key = getattr(settings, "RESEND_API_KEY", "")
    from_email = outbox.from_email or getattr(settings, "RESEND_FROM", "no-reply@niemr.app")

    payload = {
        "from": from_email,
        "to": [outbox.to],
        "cc": outbox.cc or [],
        "bcc": outbox.bcc or [],
        "subject": outbox.subject,
        "html": outbox.html or "",
        "text": outbox.text or "",
        "tags": [{"name": t, "value": "1"} for t in (outbox.tags or [])],
        "reply_to": outbox.reply_to or [],
    }
    try:
        body = json.dumps(payload).encode("utf-8")
        res = _http_post(
            "https://api.resend.com/emails",
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        data = json.loads(res)
        return data.get("id"), None
    except Exception as e:
        return None, str(e)
