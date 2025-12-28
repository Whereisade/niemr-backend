from datetime import timedelta
from django.conf import settings
from django.db import models
from django.utils import timezone

from facilities.models import Facility
from patients.models import Patient
from .enums import EncounterStatus, EncounterStage, EncounterType, Priority, SoapSection, AmendmentType

LOCK_AFTER_HOURS = 24


class Encounter(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="encounters")
    facility = models.ForeignKey(
        Facility, null=True, blank=True, on_delete=models.SET_NULL, related_name="encounters"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="encounters_created"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="encounters_updated"
    )
    
    # Workflow roles
    nurse = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="encounters_as_nurse",
        help_text="Nurse who initiated the encounter and recorded vitals",
    )
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="encounters_as_provider",
        help_text="Doctor or clinical provider responsible for diagnosis and treatment",
    )

    # meta
    encounter_type = models.CharField(max_length=16, choices=EncounterType.choices, default=EncounterType.NEW)
    status = models.CharField(max_length=16, choices=EncounterStatus.choices, default=EncounterStatus.OPEN)

    # ✅ multi-step flow
    stage = models.CharField(max_length=20, choices=EncounterStage.choices, default=EncounterStage.NOTE)

    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.ROUTINE)
    occurred_at = models.DateTimeField(help_text="When the encounter took place")

    # optional back-link
    appointment_id = models.PositiveIntegerField(null=True, blank=True)

    # subjective / objective
    chief_complaint = models.TextField(blank=True)
    duration_value = models.PositiveIntegerField(null=True, blank=True)
    duration_unit = models.CharField(max_length=16, blank=True)
    hpi = models.TextField(blank=True)
    ros = models.TextField(blank=True)
    physical_exam = models.TextField(blank=True)

    # assessment & plan
    diagnoses = models.TextField(blank=True)
    plan = models.TextField(blank=True)

    # orders (hooks—actual items live in labs/imaging/pharmacy modules)
    lab_order_ids = models.JSONField(default=list, blank=True)
    imaging_request_ids = models.JSONField(default=list, blank=True)
    prescription_ids = models.JSONField(default=list, blank=True)

    # immutability
    locked_at = models.DateTimeField(null=True, blank=True)

    # workflow timestamps
    paused_at = models.DateTimeField(null=True, blank=True)
    paused_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="encounters_paused",
    )

    resumed_at = models.DateTimeField(null=True, blank=True)
    resumed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="encounters_resumed",
    )

    labs_skipped_at = models.DateTimeField(null=True, blank=True)
    labs_skipped_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="encounters_labs_skipped",
    )

    clinical_finalized_at = models.DateTimeField(null=True, blank=True)
    clinical_finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="encounters_clinical_finalized",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["patient", "occurred_at"]),
            models.Index(fields=["facility", "occurred_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["stage"]),
        ]
        ordering = ["-occurred_at", "-id"]

    @property
    def is_locked(self) -> bool:
        """
        Lock countdown starts only after the SOAP note / diagnosis step is finalized.
        """
        if self.locked_at:
            return True
        if not self.clinical_finalized_at:
            return False
        return timezone.now() >= (self.clinical_finalized_at + timedelta(hours=LOCK_AFTER_HOURS))

    @property
    def lock_due_at(self):
        if not self.clinical_finalized_at:
            return None
        return self.clinical_finalized_at + timedelta(hours=LOCK_AFTER_HOURS)

    def maybe_lock(self):
        if not self.locked_at and self.is_locked:
            self.locked_at = timezone.now()

    def save(self, *args, **kwargs):
        # default facility from patient if present
        if not self.facility and self.patient and self.patient.facility_id:
            self.facility_id = self.patient.facility_id
        self.maybe_lock()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Encounter#{self.id} P:{self.patient_id} @ {self.occurred_at:%Y-%m-%d %H:%M}"


class EncounterAmendment(models.Model):
    encounter = models.ForeignKey(Encounter, on_delete=models.CASCADE, related_name="amendments")
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    section = models.CharField(
        max_length=32,
        choices=SoapSection.choices,
        blank=True,
        default="",
        help_text="SOAP note section being corrected (required for per-section corrections).",
    )
    amendment_type = models.CharField(
        max_length=16,
        choices=AmendmentType.choices,
        default=AmendmentType.CORRECTION,
        help_text="Type of amendment: correction replaces, addition supplements",
    )
    reason = models.CharField(max_length=255)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Amendment#{self.id} Encounter#{self.encounter_id} ({self.amendment_type})"