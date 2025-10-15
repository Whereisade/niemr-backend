import hmac, hashlib, json
from django.conf import settings
from django.utils.timezone import now
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from rest_framework import viewsets, mixins
from rest_framework.permissions import IsAuthenticated
from .models import Outbox, Template, EmailStatus
from .serializers import OutboxSerializer, TemplateSerializer

class OutboxViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin):
    queryset = Outbox.objects.all().order_by("-created_at")
    serializer_class = OutboxSerializer
    permission_classes = [IsAuthenticated]

class TemplateViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin):
    queryset = Template.objects.all().order_by("code")
    serializer_class = TemplateSerializer
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
        ob.save(update_fields=["status","delivered_at"])
    elif ev_type.endswith("bounced") or ev_type.endswith("complained"):
        ob.status = EmailStatus.BOUNCED
        ob.last_error = f"Webhook: {ev_type}"
        ob.save(update_fields=["status","last_error"])

    return JsonResponse({"ok": True})
