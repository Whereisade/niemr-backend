import uuid, json
from django.conf import settings
from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey

from .enums import Verb

class AuditLog(models.Model):
    """
    Immutable audit event for any object.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # who + request context
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events")
    actor_email = models.CharField(max_length=255, blank=True)       # snapshot convenience
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, null=True)

    # what
    verb = models.CharField(max_length=8, choices=Verb.choices)
    message = models.CharField(max_length=255, blank=True)           # short human text

    # where (target)
    target_ct = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    target_id = models.CharField(max_length=64)                      # store as str to handle UUID/str keys
    target = GenericForeignKey("target_ct", "target_id")

    # payloads
    changes = models.JSONField(default=dict, blank=True)             # {"before": {...}, "after": {...}}
    extra = models.JSONField(default=dict, blank=True)               # free-form metadata

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["actor"]),
            models.Index(fields=["actor", "created_at"]),  # ← ADD THIS for facility filtering
            models.Index(fields=["target_ct", "target_id"]),
            models.Index(fields=["verb"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.verb} {self.target_ct.model}#{self.target_id} by {self.actor_id} @ {self.created_at:%Y-%m-%d %H:%M}"
