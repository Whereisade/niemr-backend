from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone

from .enums import (
    OutreachStatus,
    Sex,
    LabOrderStatus,
    CounselingVisibility,
    BloodEligibilityStatus,
    BloodDonationOutcome,
    PregnancyStatus,
)

def _bmi(weight_kg: Decimal | None, height_cm: Decimal | None):
    if not weight_kg or not height_cm or height_cm == 0:
        return None
    h_m = (height_cm / Decimal("100"))
    return (weight_kg / (h_m * h_m)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

class OutreachEvent(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=10, choices=OutreachStatus.choices, default=OutreachStatus.DRAFT)

    # modules_enabled example:
    # {"vitals": true, "encounter": true, "lab": false, ...}
    modules_enabled = models.JSONField(default=dict, blank=True)

    patient_seq = models.PositiveIntegerField(default=0)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_events_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    closed_at = models.DateTimeField(null=True, blank=True)

    def is_module_enabled(self, module_key: str) -> bool:
        if not module_key:
            return False
        if self.status == OutreachStatus.CLOSED:
            # data still readable, but module is effectively not writable
            return bool(self.modules_enabled.get(module_key))
        return bool(self.modules_enabled.get(module_key))

    def __str__(self):
        return f"{self.title} ({self.status})"

class OutreachSite(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="sites")
    name = models.CharField(max_length=255)
    community = models.CharField(max_length=255, blank=True, default="")
    address = models.CharField(max_length=500, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("outreach_event", "name")]

    def __str__(self):
        return f"{self.name} - {self.outreach_event.title}"

class OutreachStaffProfile(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="staff_profiles")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="outreach_profiles")

    role_template = models.CharField(max_length=40, blank=True, default="")
    permissions = models.JSONField(default=list, blank=True)

    # Site assignment
    all_sites = models.BooleanField(default=True)
    sites = models.ManyToManyField(OutreachSite, blank=True, related_name="staff")

    is_active = models.BooleanField(default=True)
    disabled_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_staff_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("outreach_event", "user")]

    def disable(self):
        if not self.is_active:
            return
        self.is_active = False
        self.disabled_at = timezone.now()
        self.save(update_fields=["is_active", "disabled_at"])

    def __str__(self):
        return f"{self.user.email} @ {self.outreach_event.title}"

class OutreachPatient(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="patients")
    site = models.ForeignKey(OutreachSite, null=True, blank=True, on_delete=models.SET_NULL, related_name="patients")

    patient_code = models.CharField(max_length=32)

    full_name = models.CharField(max_length=255)
    sex = models.CharField(max_length=10, choices=Sex.choices, default=Sex.UNKNOWN)

    date_of_birth = models.DateField(null=True, blank=True)
    age_years = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(130)])

    phone = models.CharField(max_length=30, blank=True, default="")
    community = models.CharField(max_length=255, blank=True, default="")
    address = models.CharField(max_length=500, blank=True, default="")

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_patients_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("outreach_event", "patient_code")]
        indexes = [
            models.Index(fields=["outreach_event", "patient_code"]),
            models.Index(fields=["outreach_event", "full_name"]),
            models.Index(fields=["outreach_event", "phone"]),
        ]

    def __str__(self):
        return f"{self.patient_code} - {self.full_name}"

class OutreachVitals(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="vitals")
    patient = models.ForeignKey(OutreachPatient, on_delete=models.CASCADE, related_name="vitals")

    bp_sys = models.IntegerField(null=True, blank=True)
    bp_dia = models.IntegerField(null=True, blank=True)
    pulse = models.IntegerField(null=True, blank=True)
    temp_c = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    height_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    bmi = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_vitals_recorded")
    recorded_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.bmi = _bmi(self.weight_kg, self.height_cm)
        super().save(*args, **kwargs)

class OutreachEncounter(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="encounters")
    patient = models.ForeignKey(OutreachPatient, on_delete=models.CASCADE, related_name="encounters")

    complaint = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    diagnosis_tags = models.JSONField(default=list, blank=True)  # list[str]
    plan = models.TextField(blank=True, default="")
    referral_note = models.TextField(blank=True, default="")
    soap_note_attachment = models.FileField(upload_to="outreach/encounters/soap_notes/", null=True, blank=True)


    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_encounters_recorded")
    recorded_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

class OutreachLabTestCatalog(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="lab_tests")
    code = models.CharField(max_length=60)
    name = models.CharField(max_length=255)
    unit = models.CharField(max_length=60, blank=True, default="")
    ref_low = models.CharField(max_length=60, blank=True, default="")
    ref_high = models.CharField(max_length=60, blank=True, default="")
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # documentation only
    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_lab_tests_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("outreach_event", "code")]
        indexes = [
            models.Index(fields=["outreach_event", "code"]),
            models.Index(fields=["outreach_event", "name"]),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

class OutreachLabOrder(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="lab_orders")
    patient = models.ForeignKey(OutreachPatient, on_delete=models.CASCADE, related_name="lab_orders")

    status = models.CharField(max_length=20, choices=LabOrderStatus.choices, default=LabOrderStatus.ORDERED)
    notes = models.TextField(blank=True, default="")

    ordered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_lab_orders_created")
    ordered_at = models.DateTimeField(default=timezone.now)
    collected_at = models.DateTimeField(null=True, blank=True)
    result_ready_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

class OutreachLabOrderItem(models.Model):
    lab_order = models.ForeignKey(OutreachLabOrder, on_delete=models.CASCADE, related_name="items")
    test = models.ForeignKey(OutreachLabTestCatalog, null=True, blank=True, on_delete=models.SET_NULL, related_name="order_items")
    test_name = models.CharField(max_length=255, blank=True, default="")  # snapshot
    created_at = models.DateTimeField(auto_now_add=True)

class OutreachLabResult(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="lab_results")
    lab_order = models.ForeignKey(OutreachLabOrder, on_delete=models.CASCADE, related_name="results")
    item = models.ForeignKey(OutreachLabOrderItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="results")
    test_name = models.CharField(max_length=255)
    result_value = models.CharField(max_length=255, blank=True, default="")
    unit = models.CharField(max_length=60, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    result_attachment = models.FileField(upload_to="outreach/labs/results/", null=True, blank=True)

    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_lab_results_recorded")
    recorded_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

class OutreachDrugCatalog(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="drugs")
    code = models.CharField(max_length=80)
    name = models.CharField(max_length=255)
    strength = models.CharField(max_length=120, blank=True, default="")
    form = models.CharField(max_length=120, blank=True, default="")
    route = models.CharField(max_length=120, blank=True, default="")
    qty_per_unit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # documentation only
    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_drugs_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("outreach_event", "code")]
        indexes = [
            models.Index(fields=["outreach_event", "code"]),
            models.Index(fields=["outreach_event", "name"]),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

class OutreachDispense(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="dispenses")
    patient = models.ForeignKey(OutreachPatient, on_delete=models.CASCADE, related_name="dispenses")
    drug = models.ForeignKey(OutreachDrugCatalog, null=True, blank=True, on_delete=models.SET_NULL, related_name="dispenses")

    drug_name = models.CharField(max_length=255)
    strength = models.CharField(max_length=120, blank=True, default="")
    quantity = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)], default=0)
    instruction = models.TextField(blank=True, default="")

    dispensed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_dispenses_recorded")
    dispensed_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)


class OutreachVaccineCatalog(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="vaccines")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=80, blank=True, default="")
    manufacturer = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_vaccines_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("outreach_event", "name")]
        indexes = [
            models.Index(fields=["outreach_event", "name"]),
        ]

    def __str__(self):
        return self.name

class OutreachImmunization(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="immunizations")
    patient = models.ForeignKey(OutreachPatient, on_delete=models.CASCADE, related_name="immunizations")

    vaccine_name = models.CharField(max_length=255)
    dose_number = models.PositiveIntegerField(null=True, blank=True)
    batch_number = models.CharField(max_length=120, blank=True, default="")
    route = models.CharField(max_length=120, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    administered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_immunizations_recorded")
    administered_at = models.DateTimeField(default=timezone.now)

class OutreachBloodDonation(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="blood_donations")
    patient = models.ForeignKey(OutreachPatient, null=True, blank=True, on_delete=models.SET_NULL, related_name="blood_donations")

    eligibility_status = models.CharField(max_length=20, choices=BloodEligibilityStatus.choices, default=BloodEligibilityStatus.ELIGIBLE)
    deferral_reason = models.TextField(blank=True, default="")
    outcome = models.CharField(max_length=20, choices=BloodDonationOutcome.choices, default=BloodDonationOutcome.COMPLETED)
    notes = models.TextField(blank=True, default="")

    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_blood_donations_recorded")
    recorded_at = models.DateTimeField(default=timezone.now)

class OutreachCounseling(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="counseling_sessions")
    patient = models.ForeignKey(OutreachPatient, on_delete=models.CASCADE, related_name="counseling_sessions")

    topics = models.JSONField(default=list, blank=True)  # list[str]
    session_notes = models.TextField(blank=True, default="")
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)

    counselor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_counseling_recorded")
    recorded_at = models.DateTimeField(default=timezone.now)

    visibility_level = models.CharField(max_length=20, choices=CounselingVisibility.choices, default=CounselingVisibility.PRIVATE)

class OutreachMaternal(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="maternal_records")
    patient = models.ForeignKey(OutreachPatient, on_delete=models.CASCADE, related_name="maternal_records")

    pregnancy_status = models.CharField(max_length=20, choices=PregnancyStatus.choices, default=PregnancyStatus.UNKNOWN)
    gestational_age_weeks = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    risk_flags = models.JSONField(default=list, blank=True)  # list[str]
    notes = models.TextField(blank=True, default="")

    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_maternal_recorded")
    recorded_at = models.DateTimeField(default=timezone.now)

class OutreachAuditLog(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="audit_logs")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_audit_logs")
    action = models.CharField(max_length=255)
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["outreach_event", "created_at"]),
        ]

class OutreachExport(models.Model):
    outreach_event = models.ForeignKey(OutreachEvent, on_delete=models.CASCADE, related_name="exports")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_exports_created")

    export_type = models.CharField(max_length=60)  # e.g., summary, patients, lab_orders
    export_format = models.CharField(max_length=10, default="csv")  # csv|pdf|json
    filters = models.JSONField(default=dict, blank=True)

    file = models.FileField(upload_to="outreach/exports/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
