import os, resend
API_KEY = os.getenv("RESEND_API_KEY")
FROM = os.getenv("RESEND_FROM_EMAIL", "Niemr <no-reply@mail.niemr.africa>")
if API_KEY:
    resend.api_key = API_KEY

def send_email(subject: str, to: str, html: str, tags: list[str] | None = None):
    if not API_KEY:
        print(f"[EMAIL-DEV] {subject} -> {to}\n{html}")
        return {"id": "dev-msg-id"}
    payload = {"from": FROM, "to": [to], "subject": subject, "html": html}
    if tags:
        payload["tags"] = [{"name": t, "value": "1"} for t in tags]
    return resend.Emails.send(payload)
