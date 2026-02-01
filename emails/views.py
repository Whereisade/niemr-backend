import hmac, hashlib, json
from django.conf import settings
from django.utils.timezone import now
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from rest_framework import viewsets, mixins
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Outbox, Template, EmailStatus
from .serializers import OutboxSerializer, TemplateSerializer, EnquirySerializer
from emails.services.router import _attempt_send, send_email


class OutboxViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin):
    queryset = Outbox.objects.all().order_by("-created_at")
    serializer_class = OutboxSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=["post"])
    def resend(self, request, pk=None):
        """
        Manually resend an email from the outbox.

        Behaviour:
        - Reset provider-related fields.
        - Mark as QUEUED with retry_count = 0 and next_attempt_at = now().
        - Immediately attempt to send via Resend.
          On failure, it will be re-queued with exponential backoff,
          same as process_outbox.
        """
        outbox = self.get_object()

        # Reset state so we treat this as a fresh send attempt
        outbox.status = EmailStatus.QUEUED
        outbox.retry_count = 0
        outbox.next_attempt_at = now()
        outbox.last_error = ""
        outbox.provider_message_id = ""
        outbox.save(
            update_fields=[
                "status",
                "retry_count",
                "next_attempt_at",
                "last_error",
                "provider_message_id",
            ]
        )

        # Fire an immediate send; if provider fails, it will be re-queued
        _attempt_send(outbox, queue_if_failed=True)

        serializer = self.get_serializer(outbox)
        return Response(serializer.data)


class TemplateViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin):
    queryset = Template.objects.all().order_by("code")
    serializer_class = TemplateSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]


@csrf_exempt
def resend_webhook(request):
    secret = getattr(settings, "EMAILS_WEBHOOK_SECRET", "")
    if secret:
        sig = request.headers.get("X-Resend-Signature", "")
        raw = request.body
        mac = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, mac):
            return HttpResponseBadRequest("Invalid signature")

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Bad JSON")

    ev_type = payload.get("type", "")
    data = payload.get("data", {})
    email_id = data.get("email_id") or data.get("id")
    if not email_id:
        return HttpResponseBadRequest("No email id")

    ob = Outbox.objects.filter(provider_message_id=email_id).first()
    if not ob:
        return JsonResponse({"ok": True})

    if ev_type.endswith("delivered"):
        ob.status = EmailStatus.DELIVERED
        ob.delivered_at = now()
        ob.save(update_fields=["status", "delivered_at"])
    elif ev_type.endswith("bounced") or ev_type.endswith("complained"):
        ob.status = EmailStatus.BOUNCED
        ob.last_error = f"Webhook: {ev_type}"
        ob.save(update_fields=["status", "last_error"])

    return JsonResponse({"ok": True})


class EnquiryCreateView(APIView):
    """Public website enquiry.

    Accepts a small payload and sends it to the NIEMR team mailbox.
    This endpoint is intentionally public (no auth) and uses a
    honeypot field to reduce trivial bot spam.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        ser = EnquirySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        name = data.get("name", "").strip()
        email = data.get("email", "").strip()
        phone = (data.get("phone") or "").strip()
        subject = (data.get("subject") or "").strip() or "Website enquiry"
        message = (data.get("message") or "").strip()

        subject_line = f"NIEMR Enquiry: {subject}"

        # Keep the email human-friendly and easy to scan
        html = f"""
        <div style=\"font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial;line-height:1.5\">
          <h2 style=\"margin:0 0 12px\">New website enquiry</h2>
          <table style=\"border-collapse:collapse\">
            <tr><td style=\"padding:4px 10px 4px 0;font-weight:600\">Name</td><td style=\"padding:4px 0\">{name}</td></tr>
            <tr><td style=\"padding:4px 10px 4px 0;font-weight:600\">Email</td><td style=\"padding:4px 0\">{email}</td></tr>
            <tr><td style=\"padding:4px 10px 4px 0;font-weight:600\">Phone</td><td style=\"padding:4px 0\">{phone or '—'}</td></tr>
            <tr><td style=\"padding:4px 10px 4px 0;font-weight:600\">Subject</td><td style=\"padding:4px 0\">{subject}</td></tr>
          </table>
          <hr style=\"border:none;border-top:1px solid #e5e7eb;margin:16px 0\" />
          <div style=\"white-space:pre-wrap\">{message}</div>
        </div>
        """.strip()

        text = f"""New website enquiry\n\nName: {name}\nEmail: {email}\nPhone: {phone or '-'}\nSubject: {subject}\n\nMessage:\n{message}\n"""

        # Use our email service router (SMTP/Resend) to queue + send.
        send_email(
            to="niemr.ai@outlook.com",
            subject=subject_line,
            html=html,
            text=text,
            reply_to=[email] if email else [],
            tags=["website", "enquiry"],
        )

        return Response({"ok": True})
