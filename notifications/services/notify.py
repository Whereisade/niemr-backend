from typing import Iterable
from django.contrib.auth import get_user_model
from django.db import transaction
from notifications.models import Notification, Preference
from notifications.enums import Channel, Priority
from accounts.enums import UserRole

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
def notify_user(
    *,
    user: User,
    topic: str,
    title: str,
    body: str = "",
    data: dict | None = None,
    facility_id: int | None = None,
    priority: str = Priority.NORMAL,
    action_url: str = "",
    group_key: str | None = None,
    expires_at=None,
):
    """
    Create an in-app notification (always if enabled) and optionally send email if enabled.
    """
    if _is_enabled(user, topic, Channel.IN_APP):
        Notification.objects.create(
            user=user,
            facility_id=facility_id,
            topic=topic,
            priority=priority,
            title=title,
            body=body,
            data=data or {},
            action_url=action_url or "",
            group_key=group_key,
            expires_at=expires_at,
        )
    _send_email_if_enabled(user, topic, title, body)

def notify_users(
    *,
    users: Iterable[User],
    topic: str,
    title: str,
    body: str = "",
    data: dict | None = None,
    facility_id: int | None = None,
    priority: str = Priority.NORMAL,
    action_url: str = "",
    group_key: str | None = None,
    expires_at=None,
):
    for u in users:
        notify_user(
            user=u,
            topic=topic,
            title=title,
            body=body,
            data=data,
            facility_id=facility_id,
            priority=priority,
            action_url=action_url,
            group_key=group_key,
            expires_at=expires_at,
        )


def facility_staff_roles() -> list[str]:
    """Default roles considered "facility staff" for announcements."""
    return [
        UserRole.SUPER_ADMIN,
        UserRole.ADMIN,
        UserRole.FRONTDESK,
        UserRole.DOCTOR,
        UserRole.NURSE,
        UserRole.LAB,
        UserRole.PHARMACY,
    ]


def get_facility_users_for_roles(facility_id: int, roles: list[str] | None = None):
    qs = User.objects.filter(facility_id=facility_id, is_active=True)
    use_roles = roles or facility_staff_roles()
    qs = qs.filter(role__in=use_roles)
    return qs


def notify_facility_roles(
    *,
    facility_id: int,
    roles: list[str] | None,
    topic: str,
    title: str,
    body: str = "",
    data: dict | None = None,
    priority: str = Priority.NORMAL,
    action_url: str = "",
    group_key: str | None = None,
    expires_at=None,
):
    """Send the same notification to all facility users in the given roles."""
    users = get_facility_users_for_roles(facility_id, roles)
    notify_users(
        users=users,
        topic=topic,
        title=title,
        body=body,
        data=data,
        facility_id=facility_id,
        priority=priority,
        action_url=action_url,
        group_key=group_key,
        expires_at=expires_at,
    )
    try:
        return users.count()
    except Exception:
        return 0

# ─────────────────────────────────────────────────────────────
# Patient-scoped helpers
# ─────────────────────────────────────────────────────────────

def get_patient_notification_users(patient):
    """Return users to notify for a patient event.

    Includes:
      - patient.user (if present)
      - patient.guardian_user (if present)
      - parent_patient.user / parent_patient.guardian_user (if present)

    This supports dependents without user accounts (guardian should still be notified).
    """
    users = []

    try:
        if getattr(patient, 'user_id', None) and getattr(patient, 'user', None):
            users.append(patient.user)
    except Exception:
        pass

    try:
        if getattr(patient, 'guardian_user_id', None) and getattr(patient, 'guardian_user', None):
            users.append(patient.guardian_user)
    except Exception:
        pass

    try:
        parent = getattr(patient, 'parent_patient', None)
        if parent:
            if getattr(parent, 'user_id', None) and getattr(parent, 'user', None):
                users.append(parent.user)
            if getattr(parent, 'guardian_user_id', None) and getattr(parent, 'guardian_user', None):
                users.append(parent.guardian_user)
    except Exception:
        pass

    uniq = {}
    for u in users:
        try:
            uniq[getattr(u, 'id', None)] = u
        except Exception:
            pass
    return [u for k, u in uniq.items() if k]


def notify_patient(
    *,
    patient,
    topic: str,
    title: str,
    body: str = '',
    data: dict | None = None,
    facility_id: int | None = None,
    priority: str = Priority.NORMAL,
    action_url: str = '',
    group_key: str | None = None,
    expires_at=None,
):
    """Notify a patient and their guardian (if applicable)."""
    for u in get_patient_notification_users(patient):
        notify_user(
            user=u,
            topic=topic,
            title=title,
            body=body,
            data=data,
            facility_id=facility_id,
            priority=priority,
            action_url=action_url,
            group_key=group_key,
            expires_at=expires_at,
        )


def get_facility_patient_users(facility_id: int):
    """Return a unique list of patient/guardian users attached to patients in a facility.

    Note: patients are not attached to facility_id on the User model; they are attached
    via Patient.facility_id, so we have to traverse Patient records.
    """
    try:
        from patients.models import Patient
    except Exception:
        return []

    qs = (
        Patient.objects.filter(facility_id=facility_id)
        .select_related('user', 'guardian_user', 'parent_patient__user', 'parent_patient__guardian_user')
    )

    uniq = {}
    for p in qs.iterator():
        for u in get_patient_notification_users(p):
            try:
                uniq[getattr(u, 'id', None)] = u
            except Exception:
                pass
    return [u for k, u in uniq.items() if k]


def notify_facility_patients(
    *,
    facility_id: int,
    topic: str,
    title: str,
    body: str = '',
    data: dict | None = None,
    priority: str = Priority.NORMAL,
    action_url: str = '',
    group_key: str | None = None,
    expires_at=None,
):
    """Send the same notification to all patients (and guardians) in a facility."""
    users = get_facility_patient_users(facility_id)
    notify_users(
        users=users,
        topic=topic,
        title=title,
        body=body,
        data=data,
        facility_id=facility_id,
        priority=priority,
        action_url=action_url,
        group_key=group_key,
        expires_at=expires_at,
    )
    return len(users)
