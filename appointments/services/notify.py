"""appointments/services/notify.py

Email helpers for appointment-related events.

These helpers send EMAIL only when:
- the appointment allows email (Appointment.notify_email)
- and the recipient has enabled EMAIL for the matching notification topic (Preferences)

Password reset emails are handled elsewhere and are always sent.
"""

from __future__ import annotations

from django.utils import timezone

from notifications.enums import Topic
from notifications.services.notify import send_email_if_enabled, send_patient_email_if_enabled


def _fmt_dt(dt):
    try:
        if hasattr(dt, "astimezone"):
            dt = timezone.localtime(dt)
        return str(dt)
    except Exception:
        return str(dt)


def _patient_name(appt):
    patient = getattr(appt, "patient", None)
    if not patient:
        return ""
    try:
        return f"{getattr(patient, 'first_name', '')} {getattr(patient, 'last_name', '')}".strip()
    except Exception:
        return ""


def _reason(appt):
    return getattr(appt, "reason", "") or ""


def send_confirmation(appt):
    """Patient confirmation (opt-in via notify_email + email preferences)."""
    if not getattr(appt, "notify_email", False):
        return

    when = _fmt_dt(getattr(appt, "start_at", ""))
    html = f"""<p>Your appointment is scheduled for <b>{when}</b>.</p>"""
    send_patient_email_if_enabled(
        patient=getattr(appt, "patient", None),
        topic=Topic.APPOINTMENT_CONFIRMED,
        subject="Appointment scheduled",
        html=html,
        tags=["appointment.confirmed"],
        allow_email=True,
    )


def send_reminder(appt):
    """Patient reminder (opt-in via notify_email + email preferences)."""
    if not getattr(appt, "notify_email", False):
        return

    when = _fmt_dt(getattr(appt, "start_at", ""))
    html = f"""<p>Reminder: you have an appointment at <b>{when}</b>.</p>"""
    send_patient_email_if_enabled(
        patient=getattr(appt, "patient", None),
        topic=Topic.APPOINTMENT_REMINDER,
        subject="Appointment reminder",
        html=html,
        tags=["appointment.reminder"],
        allow_email=True,
    )


def send_cancelled(appt):
    """Patient cancellation notice (opt-in via notify_email + email preferences)."""
    if not getattr(appt, "notify_email", False):
        return

    when = _fmt_dt(getattr(appt, "start_at", ""))
    html = f"""<p>Your appointment (#{appt.id}) scheduled for <b>{when}</b> was cancelled.</p>"""
    send_patient_email_if_enabled(
        patient=getattr(appt, "patient", None),
        topic=Topic.APPOINTMENT_CANCELLED,
        subject="Appointment cancelled",
        html=html,
        tags=["appointment.cancelled"],
        allow_email=True,
    )


def send_no_show(appt):
    """Patient no-show notice (opt-in via notify_email + email preferences)."""
    if not getattr(appt, "notify_email", False):
        return

    when = _fmt_dt(getattr(appt, "start_at", ""))
    html = f"""<p>You missed your appointment (#{appt.id}) scheduled for <b>{when}</b>.</p>"""
    send_patient_email_if_enabled(
        patient=getattr(appt, "patient", None),
        topic=Topic.APPOINTMENT_NO_SHOW,
        subject="Missed appointment",
        html=html,
        tags=["appointment.no_show"],
        allow_email=True,
    )


def send_completed(appt):
    """Patient completed notice (opt-in via notify_email + email preferences)."""
    if not getattr(appt, "notify_email", False):
        return

    html = f"""<p>Your appointment (#{appt.id}) has been completed.</p>"""
    send_patient_email_if_enabled(
        patient=getattr(appt, "patient", None),
        topic=Topic.APPOINTMENT_COMPLETED,
        subject="Appointment completed",
        html=html,
        tags=["appointment.completed"],
        allow_email=True,
    )


def send_provider_assignment(appt):
    """Provider assignment email (controlled by provider EMAIL preferences)."""
    provider = getattr(appt, "provider", None)
    if not provider or not getattr(provider, "email", None):
        return

    patient_name = _patient_name(appt) or "a patient"
    when = _fmt_dt(getattr(appt, "start_at", ""))
    reason = _reason(appt)

    html = f"""<p>You have been assigned a new appointment.</p>
<p><b>Patient:</b> {patient_name}</p>
<p><b>When:</b> {when}</p>
<p><b>Reason:</b> {reason}</p>
"""

    send_email_if_enabled(
        user=provider,
        topic=Topic.STAFF_ASSIGNED,
        subject="You have been assigned an appointment",
        html=html,
        tags=["staff.assigned"],
        allow_email=True,
    )
