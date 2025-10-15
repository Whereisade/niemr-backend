from typing import Iterable
from django.contrib.auth import get_user_model
from django.db import transaction
from notifications.models import Notification, Preference
from notifications.enums import Channel, Topic

User = get_user_model()

def _is_enabled(user: User, topic: str, channel: str) -> bool:
    pref = Preference.objects.filter(user=user, topic=topic, channel=channel).first()
    if pref is None:
        # defaults: IN_APP enabled; EMAIL disabled
        return channel == Channel.IN_APP
    return pref.enabled

def _send_email_if_enabled(user: User, topic: str, title: str, body: str):
    if not _is_enabled(user, topic, Channel.EMAIL):
        return
    if not user.email:
        return
    try:
        # Delegate to emails module if present; otherwise no-op.
        from emails.services.router import send_email
        send_email(to=user.email, subject=title, html=f"<p>{body}</p>", tags=[topic.lower()])
    except Exception:
        pass

@transaction.atomic
def notify_user(*, user: User, topic: str, title: str, body: str = "", data: dict | None = None, facility_id: int | None = None):
    """
    Create an in-app notification (always if enabled) and optionally send email if enabled.
    """
    if _is_enabled(user, topic, Channel.IN_APP):
        Notification.objects.create(
            user=user, facility_id=facility_id, topic=topic, title=title, body=body, data=data or {}
        )
    _send_email_if_enabled(user, topic, title, body)

def notify_users(*, users: Iterable[User], topic: str, title: str, body: str = "", data: dict | None = None, facility_id: int | None = None):
    for u in users:
        notify_user(user=u, topic=topic, title=title, body=body, data=data, facility_id=facility_id)
