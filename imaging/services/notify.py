def notify_report_ready(patient_email: str, request_id: int):
    try:
        from emails.services.router import send_email
        send_email(
            to=patient_email,
            subject="Your imaging report is ready",
            html=f"<p>Your imaging request #{request_id} has a completed report.</p>",
            tags=["result.ready"]
        )
    except Exception:
        pass
