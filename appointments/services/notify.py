def send_appt_email(to: str, subject: str, html: str, tags=None):
    try:
        from emails.services.router import send_email
        send_email(to=to, subject=subject, html=html, tags=tags or ["patient.reminder"])
    except Exception:
        # dev/no-op
        pass

def send_confirmation(appt):
    if appt.notify_email and appt.patient.email:
        send_appt_email(
            to=appt.patient.email,
            subject="Appointment scheduled",
            html=f"<p>Your appointment is scheduled for {appt.start_at}.</p>",
        )

def send_reminder(appt):
    if appt.notify_email and appt.patient.email:
        send_appt_email(
            to=appt.patient.email,
            subject="Appointment reminder",
            html=f"<p>Reminder: you have an appointment at {appt.start_at}.</p>",
        )
