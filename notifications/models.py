from django.conf import settings
from django.db import models
from django.utils import timezone
from facilities.models import Facility
from .enums import Channel, Topic

class Preference(models.Model):
    """
    Per-user notification preferences by topic and channel.
    If no row exists for a (user, topic), assume IN_APP enabled, EMAIL disabled by default.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notif_prefs")
    topic = models.CharField(max_length=40, choices=Topic.choices)
    channel = models.CharField(max_length=16, choices=Channel.choices, default=Channel.IN_APP)
    enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = ("user","topic","channel")

class Notification(models.Model):
    """
    A single notification delivered to exactly one user.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    facility = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.SET_NULL)

    topic = models.CharField(max_length=40, choices=Topic.choices, default=Topic.GENERAL)
    title = models.CharField(max_length=140)
    body = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)  # arbitrary payload (IDs, routes, etc.)

    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user","is_read","created_at"]),
            models.Index(fields=["facility","created_at"]),
            models.Index(fields=["topic"]),
        ]
        ordering = ["-created_at","-id"]

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read","read_at"])
