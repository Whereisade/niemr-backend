from django.contrib.contenttypes.models import ContentType
from .models import AuditLog
from .enums import Verb
from .local import get_request

def log_action(*, obj, title: str, extra: dict | None = None):
    req = get_request()
    user = getattr(req, "user", None)
    AuditLog.objects.create(
        actor=(user if getattr(user, "is_authenticated", False) else None),
        actor_email=(getattr(user, "email", "") if getattr(user, "is_authenticated", False) else ""),
        ip_address=(getattr(req, "META", {}).get("REMOTE_ADDR") if req else None),
        user_agent=(getattr(req, "META", {}).get("HTTP_USER_AGENT") if req else None),
        verb=Verb.ACTION,
        message=title[:255],
        target_ct=ContentType.objects.get_for_model(obj.__class__),
        target_id=str(obj.pk),
        extra=extra or {},
    )
