from django.conf import settings
from django.db import models
from django.utils import timezone

class ReportType(models.TextChoices):
    ENCOUNTER = "ENCOUNTER", "Encounter Summary"
    LAB       = "LAB",       "Lab Result"
    IMAGING   = "IMAGING",   "Imaging Report"
    BILLING   = "BILLING",   "Billing Statement"

class ReportJob(models.Model):
    """
    Optional audit/persistence of generated reports. Stores a pointer to an attachment if saved.
    """
    report_type = models.CharField(max_length=16, choices=ReportType.choices)
    ref_id = models.PositiveIntegerField()  # encounter_id, lab_order_id, imaging_request_id, patient_id (for billing)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    format = models.CharField(max_length=8, default="PDF")  # PDF/HTML
    saved_as_attachment_id = models.IntegerField(null=True, blank=True)  # attachments.File.id
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at","-id"]
