from django.db.models.signals import pre_save, post_save, pre_delete, m2m_changed
from django.dispatch import receiver
import sys
from django.db import connection, OperationalError

import json
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models.fields.files import FieldFile

from django.contrib.contenttypes.models import ContentType
from django.db.models import Model, ManyToManyField

from .models import AuditLog
from .enums import Verb
from .local import get_request

AUDIT_APPS = None  # set to a list like ["patients","vitals",...] to restrict; None = all apps


class SafeJSONEncoder(DjangoJSONEncoder):
    def default(self, o):
        # FieldFile -> stored name or None
        if isinstance(o, FieldFile):
            return o.name or None

        # sets -> lists
        if isinstance(o, set):
            return list(o)

        # Any Django model instance -> primary key (or string fallback)
        if isinstance(o, Model):
            # We already store content_type + object_id separately,
            # so just reduce model instances to their primary key.
            return o.pk or str(o)

        return super().default(o)


def json_ready(payload: dict):
    # Use SafeJSONEncoder so files and sets don’t crash dumps()
    return json.loads(json.dumps(payload, cls=SafeJSONEncoder))


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


def safe_model_dict(instance) -> dict:
    """Return a dict of concrete field values for an instance.

    - Skips reverse relations and m2m
    - Converts FileField values to their stored name (or None)
    """
    data = {}
    for f in instance._meta.get_fields():
        # Skip reverse relations and m2m
        if f.many_to_many or f.one_to_many or getattr(f, "auto_created", False):
            continue

        # Only concrete fields
        if not hasattr(f, "attname"):
            continue

        val = getattr(instance, f.attname, None)

        # File/ImageField → stored name
        if isinstance(f, models.FileField):
            data[f.name] = val.name if isinstance(val, FieldFile) else (val or None)
            continue

        # Normal fields
        try:
            data[f.name] = f.value_from_object(instance)
        except Exception:
            data[f.name] = val
    return data


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
        after_payload = json_ready({"after": safe_model_dict(instance)})
        AuditLog.objects.create(
            actor=ctx["user"], actor_email=ctx["email"],
            ip_address=ctx["ip"], user_agent=ctx["ua"],
            verb=Verb.CREATE, message="Created",
            target_ct=ct, target_id=str(instance.pk),
            changes=after_payload,
        )
    else:
        before = getattr(instance, "__audit_before__", None) or {}
        after = safe_model_dict(instance)
        if before != after:
            changes_payload = json_ready({"before": before, "after": after})
            AuditLog.objects.create(
                actor=ctx["user"], actor_email=ctx["email"],
                ip_address=ctx["ip"], user_agent=ctx["ua"],
                verb=Verb.UPDATE, message="Updated",
                target_ct=ct, target_id=str(instance.pk),
                changes=changes_payload,
            )


@receiver(pre_delete)
def audit_pre_delete(sender, instance, **kwargs):
    if _during_migration() or _skip_sender(sender) or not _contenttypes_ready():
        return
    if not _should_audit(instance):
        return
    ct = ContentType.objects.get_for_model(instance.__class__)
    ctx = _ctx()
    before_payload = json_ready({"before": safe_model_dict(instance)})
    AuditLog.objects.create(
        actor=ctx["user"], actor_email=ctx["email"],
        ip_address=ctx["ip"], user_agent=ctx["ua"],
        verb=Verb.DELETE, message="Deleted",
        target_ct=ct, target_id=str(instance.pk),
        changes=before_payload,
    )


@receiver(m2m_changed)
def audit_m2m(sender, instance, action, reverse, model, pk_set, **kwargs):
    if _during_migration() or _skip_sender(instance.__class__) or not _contenttypes_ready():
        return
    if "through" in sender.__name__.lower():  # typical
        pass
    if not _should_audit(instance):
        return
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    ct = ContentType.objects.get_for_model(instance.__class__)
    ctx = _ctx()
    m2m_payload = json_ready({
        "related_model": model._meta.label_lower,
        "pks": list(pk_set) if pk_set else [],
    })
    AuditLog.objects.create(
        actor=ctx["user"], actor_email=ctx["email"],
        ip_address=ctx["ip"], user_agent=ctx["ua"],
        verb=Verb.M2M, message=f"M2M {action}",
        target_ct=ct, target_id=str(instance.pk),
        changes=m2m_payload,
    )
