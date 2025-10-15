def notify_result_ready(patient_email: str, order_id: int):
    try:
        # Prefer a centralized emails app if present
        from emails.services.router import send_email
        send_email(
            to=patient_email,
            subject="Your lab result is ready",
            html=f"<p>Your lab order #{order_id} has results available.</p>",
            tags=["result.ready"]
        )
    except Exception:
        # dev/no-op
        pass
