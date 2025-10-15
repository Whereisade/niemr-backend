from django.db import models
from django.utils import timezone

class EmailStatus(models.TextChoices):
    QUEUED     = "QUEUED", "Queued"
    SENDING    = "SENDING", "Sending"
    SENT       = "SENT", "Sent"
    DELIVERED  = "DELIVERED", "Delivered"
    BOUNCED    = "BOUNCED", "Bounced"
    FAILED     = "FAILED", "Failed"

class Template(models.Model):
    code = models.CharField(max_length=64, unique=True)
    subject = models.CharField(max_length=140)
    html = models.TextField()
    text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.code

class Outbox(models.Model):
    to = models.EmailField()
    cc = models.JSONField(default=list, blank=True)
    bcc = models.JSONField(default=list, blank=True)
    subject = models.CharField(max_length=140)
    html = models.TextField(blank=True)
    text = models.TextField(blank=True)
    from_email = models.EmailField(blank=True)
    reply_to = models.JSONField(default=list, blank=True)
    tags = models.JSONField(default=list, blank=True)
    template_code = models.CharField(max_length=64, blank=True)
    template_data = models.JSONField(default=dict, blank=True)
    attachment_file_ids = models.JSONField(default=list, blank=True)
    provider_message_id = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=12, choices=EmailStatus.choices, default=EmailStatus.QUEUED)
    last_error = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    def __str__(self): return f"{self.subject} -> {self.to}"
