from django.conf import settings
from django.db import models
from django.utils import timezone
from facilities.models import Facility
from .enums import Channel, Topic, Priority
from patients.models import Patient

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
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    title = models.CharField(max_length=140)
    body = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)  # arbitrary payload (IDs, routes, etc.)

    # Optional UX helpers
    action_url = models.CharField(max_length=240, blank=True)
    group_key = models.CharField(max_length=80, null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user","is_read","created_at"]),
            models.Index(fields=["user","archived","is_read","created_at"]),
            models.Index(fields=["facility","created_at"]),
            models.Index(fields=["topic"]),
            models.Index(fields=["priority"]),
            models.Index(fields=["group_key"]),
        ]
        ordering = ["-created_at","-id"]

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read","read_at"])

    def mark_unread(self):
        if self.is_read:
            self.is_read = False
            self.read_at = None
            self.save(update_fields=["is_read","read_at"])

    def archive(self):
        if not self.archived:
            self.archived = True
            self.archived_at = timezone.now()
            self.save(update_fields=["archived","archived_at"])

    def unarchive(self):
        if self.archived:
            self.archived = False
            self.archived_at = None
            self.save(update_fields=["archived","archived_at"])

    @property
    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= timezone.now())


class FacilityAnnouncement(models.Model):
    """Facility-scoped broadcast announcement.

    This is a canonical record (audit + history). When an announcement is created,
    we typically fan it out into per-user Notification rows for the target audience.
    """

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="announcements")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="announcements_created",
    )

    topic = models.CharField(max_length=40, choices=Topic.choices, default=Topic.SYSTEM_ANNOUNCEMENT)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)

    title = models.CharField(max_length=140)
    body = models.TextField(blank=True)
    action_url = models.CharField(max_length=240, blank=True, default="")

    # List of roles (e.g. ["FRONTDESK", "NURSE"]). Empty means "all facility staff".
    audience_roles = models.JSONField(default=list, blank=True)

    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    sent_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["facility", "is_active", "created_at"], name="ann_fac_active_created"),
        ]

    @property
    def is_current(self):
        now = timezone.now()
        if not self.is_active:
            return False
        if self.starts_at and self.starts_at > now:
            return False
        if self.ends_at and self.ends_at <= now:
            return False
        return True

class Reminder(models.Model):
    class ReminderType(models.TextChoices):
        VITALS = "VITALS", "Vitals"
        MEDICATION = "MEDICATION", "Medication"
        LAB = "LAB", "Lab Test"
        APPOINTMENT = "APPOINTMENT", "Appointment"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SENT = "SENT", "Sent"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
        DISMISSED = "DISMISSED", "Dismissed"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="reminders")
    nurse = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    reminder_type = models.CharField(
        max_length=32, choices=ReminderType.choices, default=ReminderType.OTHER
    )
    message = models.TextField()
    reminder_time = models.DateTimeField()

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    sent_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Reminder for {self.patient.full_name} - {self.reminder_type}"

    class Meta:
        ordering = ['reminder_time']