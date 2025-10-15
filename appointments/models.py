from django.conf import settings
from django.db import models
from django.utils import timezone
from facilities.models import Facility
from patients.models import Patient
from .enums import ApptType, ApptStatus

class Appointment(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="appointments")
    facility = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.SET_NULL, related_name="appointments")
    provider = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="appointments_provided")  # doctor/nurse/etc.
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="appointments_created")

    appt_type = models.CharField(max_length=16, choices=ApptType.choices, default=ApptType.CONSULT)
    status    = models.CharField(max_length=16, choices=ApptStatus.choices, default=ApptStatus.SCHEDULED)

    reason = models.CharField(max_length=255, blank=True)
    notes  = models.TextField(blank=True)

    start_at = models.DateTimeField()
    end_at   = models.DateTimeField()

    # optional back-links
    encounter_id = models.PositiveIntegerField(null=True, blank=True)
    lab_order_id = models.PositiveIntegerField(null=True, blank=True)
    imaging_request_id = models.PositiveIntegerField(null=True, blank=True)

    # reminders
    notify_email = models.BooleanField(default=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["facility","start_at"]),
            models.Index(fields=["provider","start_at"]),
            models.Index(fields=["patient","start_at"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["start_at","id"]

    def __str__(self):
        return f"Appt#{self.id} P:{self.patient_id} {self.start_at:%Y-%m-%d %H:%M} ({self.appt_type})"
