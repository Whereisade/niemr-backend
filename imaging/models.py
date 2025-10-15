from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from facilities.models import Facility
from patients.models import Patient
from .enums import Modality, RequestStatus, Priority

class ImagingProcedure(models.Model):
    code = models.CharField(max_length=40, unique=True)   # e.g., CXR, US-ABD
    name = models.CharField(max_length=160)
    modality = models.CharField(max_length=8, choices=Modality.choices)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.code} - {self.name} ({self.modality})"

class ImagingRequest(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="imaging_requests")
    facility = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.SET_NULL, related_name="imaging_requests")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="imaging_requests_created")
    procedure = models.ForeignKey(ImagingProcedure, on_delete=models.PROTECT)

    priority = models.CharField(max_length=12, choices=Priority.choices, default=Priority.ROUTINE)
    status = models.CharField(max_length=12, choices=RequestStatus.choices, default=RequestStatus.REQUESTED)

    indication = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    encounter_id = models.PositiveIntegerField(null=True, blank=True)  # optional: back-link to Encounter

    external_center_name = models.CharField(max_length=160, blank=True)  # if referred outside

    class Meta:
        indexes = [
            models.Index(fields=["patient","requested_at"]),
            models.Index(fields=["facility","requested_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["procedure"]),
        ]
        ordering = ["-requested_at","-id"]

    def __str__(self):
        return f"ImagingReq#{self.id} {self.procedure.code} P:{self.patient_id}"

class ImagingReport(models.Model):
    """
    Final report. Assets are stored in a simple local model now;
    later we can migrate to the dedicated `attachments` app.
    """
    request = models.OneToOneField(ImagingRequest, on_delete=models.CASCADE, related_name="report")
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="imaging_reports_authored")
    findings = models.TextField()
    impression = models.TextField()
    reported_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Report#{self.id} Req:{self.request_id}"

class ImagingAsset(models.Model):
    report = models.ForeignKey(ImagingReport, on_delete=models.CASCADE, related_name="assets")
    kind = models.CharField(max_length=32, blank=True)  # e.g., "DICOM", "JPG", "PDF"
    file = models.FileField(upload_to="imaging_assets/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
