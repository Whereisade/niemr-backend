"""appointments/services/notify.py

Email helpers for appointment-related events.

This module uses the project-wide emails router (`emails.services.router.send_email`)
so it works with Google SMTP (EMAILS_PROVIDER=SMTP) or Resend (EMAILS_PROVIDER=RESEND).
"""

from __future__ import annotations

from django.utils import timezone


def _fmt_dt(dt):
    try:
        # Make logs/emails more readable; keeps timezone info if present.
        if hasattr(dt, "astimezone"):
            return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    return str(dt)


def send_appt_email(to: str, subject: str, html: str, tags=None):
    try:
        from emails.services.router import send_email

        send_email(to=to, subject=subject, html=html, tags=tags or [])
    except Exception:
        # dev/no-op
        pass


def send_confirmation(appt):
    """Patient confirmation (opt-in via notify_email)."""
    if getattr(appt, "notify_email", False) and getattr(getattr(appt, "patient", None), "email", None):
        send_appt_email(
            to=appt.patient.email,
            subject="Appointment scheduled",
            html=f"<p>Your appointment is scheduled for {_fmt_dt(appt.start_at)}.</p>",
            tags=["appointment.confirmed"],
        )


def send_reminder(appt):
    """Patient reminder (opt-in via notify_email)."""
    if getattr(appt, "notify_email", False) and getattr(getattr(appt, "patient", None), "email", None):
        send_appt_email(
            to=appt.patient.email,
            subject="Appointment reminder",
            html=f"<p>Reminder: you have an appointment at {_fmt_dt(appt.start_at)}.</p>",
            tags=["appointment.reminder"],
        )


def send_provider_assignment(appt):
    """Provider assignment email (doctor/nurse/lab/pharmacy staff)"""
    provider = getattr(appt, "provider", None)
    if not provider or not getattr(provider, "email", None):
        return

    patient = getattr(appt, "patient", None)
    patient_name = ""
    try:
        if patient:
            patient_name = f"{getattr(patient, 'first_name', '')} {getattr(patient, 'last_name', '')}".strip()
    except Exception:
        patient_name = ""

    when = _fmt_dt(getattr(appt, "start_at", ""))
    reason = getattr(appt, "reason", "") or ""

    send_appt_email(
        to=provider.email,
        subject="You have been assigned an appointment",
        html=(
            "<p>You have been assigned a new appointment.</p>"
            + (f"<p><b>Patient:</b> {patient_name}</p>" if patient_name else "")
            + f"<p><b>Time:</b> {when}</p>"
            + (f"<p><b>Reason:</b> {reason}</p>" if reason else "")
            + f"<p><b>Appointment ID:</b> {appt.id}</p>"
        ),
        tags=["staff.appointment_assigned"],
    )


def send_completed(appt):
    """Patient completion email (opt-in via notify_email)."""
    if getattr(appt, "notify_email", False) and getattr(getattr(appt, "patient", None), "email", None):
        send_appt_email(
            to=appt.patient.email,
            subject="Appointment completed",
            html=f"<p>Your appointment (#{appt.id}) on {_fmt_dt(appt.start_at)} has been completed.</p>",
            tags=["appointment.completed"],
        )


def send_cancelled(appt):
    """Patient cancellation email (opt-in via notify_email)."""
    if getattr(appt, "notify_email", False) and getattr(getattr(appt, "patient", None), "email", None):
        send_appt_email(
            to=appt.patient.email,
            subject="Appointment cancelled",
            html=f"<p>Your appointment (#{appt.id}) scheduled for {_fmt_dt(appt.start_at)} was cancelled.</p>",
            tags=["appointment.cancelled"],
        )


def send_no_show(appt):
    """Patient no-show notice (opt-in via notify_email)."""
    if getattr(appt, "notify_email", False) and getattr(getattr(appt, "patient", None), "email", None):
        send_appt_email(
            to=appt.patient.email,
            subject="Missed appointment",
            html=f"<p>You missed your appointment (#{appt.id}) scheduled for {_fmt_dt(appt.start_at)}.</p>",
            tags=["appointment.no_show"],
        )
