from django.db.models.signals import pre_save, post_save, pre_delete, m2m_changed
from django.dispatch import receiver
import sys
from django.db import connection, OperationalError

from django.contrib.contenttypes.models import ContentType
from django.db.models import Model, ManyToManyField

from .models import AuditLog
from .enums import Verb
from .local import get_request
from .utils import safe_model_dict

AUDIT_APPS = None  # set to a list like ["patients","vitals",...] to restrict; None = all apps

def _ctx():
    req = get_request()
    user = getattr(req, "user", None)
    return {
        "user": (user if getattr(user, "is_authenticated", False) else None),
        "email": (getattr(user, "email", "") if getattr(user, "is_authenticated", False) else ""),
        "ip": getattr(req, "META", {}).get("REMOTE_ADDR") if req else None,
        "ua": getattr(req, "META", {}).get("HTTP_USER_AGENT") if req else None,
    }

def _should_audit(instance: Model) -> bool:
    if AUDIT_APPS is None:
        return True
    return instance._meta.app_label in AUDIT_APPS

def _during_migration() -> bool:
    # Don’t emit audit logs while running migrations
    return any(cmd in sys.argv for cmd in ("migrate", "makemigrations"))

def _contenttypes_ready() -> bool:
    # Be defensive: contenttypes table might not exist yet
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1 FROM django_content_type LIMIT 1")
        return True
    except Exception:
        return False

def _skip_sender(sender) -> bool:
    # Avoid logging our own AuditLog saves and a few framework tables
    label = f"{sender._meta.app_label}.{sender._meta.model_name}"
    return label in {
        "audit.auditlog",
        "contenttypes.contenttype",
        "sessions.session",
        "admin.logentry",
        "auth.permission",
        "auth.group",
    }

@receiver(pre_save)
def audit_pre_save(sender, instance, **kwargs):
    if _during_migration() or _skip_sender(sender) or not _contenttypes_ready():
        return
    if not _should_audit(instance) or instance._state.adding:
        return
    # capture "before"
    try:
        old = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return
    instance.__audit_before__ = safe_model_dict(old)

@receiver(post_save)
def audit_post_save(sender, instance, created, **kwargs):
    if _during_migration() or _skip_sender(sender) or not _contenttypes_ready():
        return
    if not _should_audit(instance):
        return
    ct = ContentType.objects.get_for_model(instance.__class__)
    ctx = _ctx()
    if created:
        AuditLog.objects.create(
            actor=ctx["user"], actor_email=ctx["email"],
            ip_address=ctx["ip"], user_agent=ctx["ua"],
            verb=Verb.CREATE, message="Created",
            target_ct=ct, target_id=str(instance.pk),
            changes={"after": safe_model_dict(instance)},
        )
    else:
        before = getattr(instance, "__audit_before__", None) or {}
        after = safe_model_dict(instance)
        if before != after:
            AuditLog.objects.create(
                actor=ctx["user"], actor_email=ctx["email"],
                ip_address=ctx["ip"], user_agent=ctx["ua"],
                verb=Verb.UPDATE, message="Updated",
                target_ct=ct, target_id=str(instance.pk),
                changes={"before": before, "after": after},
            )

@receiver(pre_delete)
def audit_pre_delete(sender, instance, **kwargs):
    if _during_migration() or _skip_sender(sender) or not _contenttypes_ready():
        return
    if not _should_audit(instance):
        return
    ct = ContentType.objects.get_for_model(instance.__class__)
    ctx = _ctx()
    AuditLog.objects.create(
        actor=ctx["user"], actor_email=ctx["email"],
        ip_address=ctx["ip"], user_agent=ctx["ua"],
        verb=Verb.DELETE, message="Deleted",
        target_ct=ct, target_id=str(instance.pk),
        changes={"before": safe_model_dict(instance)},
    )

@receiver(m2m_changed)
def audit_m2m(sender, instance, action, reverse, model, pk_set, **kwargs):
    if _during_migration() or _skip_sender(instance.__class__) or not _contenttypes_ready():
        return
    if "through" in sender.__name__.lower():  # typical
        pass
    if not _should_audit(instance):
        return
    if action not in ("post_add","post_remove","post_clear"):
        return
    ct = ContentType.objects.get_for_model(instance.__class__)
    ctx = _ctx()
    AuditLog.objects.create(
        actor=ctx["user"], actor_email=ctx["email"],
        ip_address=ctx["ip"], user_agent=ctx["ua"],
        verb=Verb.M2M, message=f"M2M {action}",
        target_ct=ct, target_id=str(instance.pk),
        changes={"related_model": model._meta.label_lower, "pks": list(pk_set) if pk_set else []},
    )
