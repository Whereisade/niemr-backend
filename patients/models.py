from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
import uuid
from django.core.validators import RegexValidator, MinValueValidator
from django.db import models
from django.core.exceptions import ValidationError
from facilities.models import Facility
from .enums import (PatientStatus, EncounterType, BloodGroup, Genotype, InsuranceStatus)

phone_validator = RegexValidator(
    regex=r"^\+\d{1,3}\d{6,14}$",
    message="Phone must be E.164 format (e.g. +2348012345678).",
)

class HMO(models.Model):
    name = models.CharField(max_length=160, unique=True)
    def __str__(self): return self.name

def patient_document_upload_path(instance, filename):
    # e.g. patient_documents/<patient_id>/<uuid>_<filename>
    return f"patient_documents/{instance.patient_id}/{uuid.uuid4()}_{filename}"

class Patient(models.Model):
    # ownership / scoping
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="patient_profile",)
    facility = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.SET_NULL, related_name="patients")
    guardian_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="guardian_dependents")  # parent/guardian
    # self-referential parent (a Patient can be a dependent of another Patient)
    parent_patient = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="dependents",
        on_delete=models.PROTECT,  # require reassignment/removal before deleting guardian
        help_text="If set, this Patient is a dependent of `parent_patient`."
    )

    relationship_to_guardian = models.CharField(
        max_length=64,
        blank=True,
        help_text="Relationship of this dependent to their parent/guardian, e.g. Son, Daughter, Spouse.",
    )

    # core demographics
    first_name = models.CharField(max_length=120)
    last_name  = models.CharField(max_length=120)
    middle_name = models.CharField(max_length=120, blank=True)
    dob = models.DateField()
    gender = models.CharField(max_length=32, blank=True)

    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, validators=[phone_validator], blank=True)

    country = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    lga = models.CharField(max_length=120, blank=True)
    address = models.TextField(blank=True)

    # insurance
    insurance_status = models.CharField(max_length=16, choices=InsuranceStatus.choices, default=InsuranceStatus.SELF_PAY)
    hmo = models.ForeignKey(HMO, null=True, blank=True, on_delete=models.SET_NULL)
    hmo_plan = models.CharField(max_length=120, blank=True)

    # clinical profile bits
    blood_group = models.CharField(max_length=8, choices=BloodGroup.choices, blank=True)
    blood_group_other = models.CharField(max_length=3, blank=True)
    genotype = models.CharField(max_length=8, choices=Genotype.choices, blank=True)
    genotype_other = models.CharField(max_length=2, blank=True)

    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.0"))])
    height_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.0"))])
    bmi = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)  # auto-calculated

    # status defaults
    patient_status = models.CharField(max_length=16, choices=PatientStatus.choices, default=PatientStatus.OUTPATIENT)
    default_encounter_type = models.CharField(max_length=16, choices=EncounterType.choices, default=EncounterType.NEW)

    # emergency contact
    emergency_contact_name = models.CharField(max_length=120, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, validators=[phone_validator], blank=True)

    # timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def _calc_bmi(self):
        if not self.weight_kg or not self.height_cm or self.height_cm == 0:
            return None
        h_m = (self.height_cm / Decimal("100"))
        bmi = (self.weight_kg / (h_m * h_m)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return bmi

    def clean(self):
        # self-parent validation
        if self.parent_patient_id and self.parent_patient_id == self.id:
            raise ValidationError("A patient cannot be their own parent.")

        # prevent multi-level nesting 
        if self.parent_patient and self.parent_patient.parent_patient_id:
            raise ValidationError("Dependents cannot be nested more than one level.")

        # facility alignment validation
        if self.parent_patient_id:
            if getattr(self, "facility_id", None) and self.facility_id != self.parent_patient.facility_id:
                raise ValidationError("Dependent must belong to the same facility as guardian.")

    def save(self, *args, **kwargs):
        # inherit scoping from guardian
        if self.parent_patient_id:
            if hasattr(self, "facility_id"):
                self.facility_id = self.parent_patient.facility_id
            if hasattr(self, "enterprise_id") and hasattr(self.parent_patient, "enterprise_id"):
                self.enterprise_id = self.parent_patient.enterprise_id
                
        # existing calculations
        self.bmi = self._calc_bmi()
        if self.blood_group != BloodGroup.OTHER:
            self.blood_group_other = ""
        if self.genotype != Genotype.OTHER:
            self.genotype_other = ""
            
        super().save(*args, **kwargs)

    @property
    def is_dependent(self) -> bool:
        """True if this patient is a dependent of another patient."""
        return bool(self.parent_patient_id)

    def __str__(self):
        return f"{self.last_name}, {self.first_name}"
    
class PatientDocument(models.Model):
    class DocumentType(models.TextChoices):
        BLOOD_TEST = "BLOOD_TEST", "Blood test"
        XRAY = "XRAY", "X-ray"
        ULTRASOUND = "ULTRASOUND", "Ultrasound"
        DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY", "Discharge summary"
        REFERRAL_NOTE = "REFERRAL_NOTE", "Referral note"
        LAB_RESULT = "LAB_RESULT", "Lab result"
        CT_SCAN = "CT_SCAN", "CT scan"
        OTHER = "OTHER", "Other"

    class UploadedBy(models.TextChoices):
        PATIENT = "PATIENT", "Uploaded by patient"
        DOCTOR = "DOCTOR", "Uploaded by doctor"
        NURSE = "NURSE", "Uploaded by nurse"
        ADMIN = "ADMIN", "Uploaded by admin"
        SYSTEM = "SYSTEM", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # **Key point**: documents are attached to the patient record
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="documents",
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="patient_documents",
    )

    uploaded_by_role = models.CharField(
        max_length=20,
        choices=UploadedBy.choices,
        default=UploadedBy.PATIENT,  # docs tagged “Uploaded by Patient” by default
    )

    title = models.CharField(max_length=255, blank=True)
    document_type = models.CharField(
        max_length=32,
        choices=DocumentType.choices,
        default=DocumentType.OTHER,
    )
    file = models.FileField(upload_to=patient_document_upload_path)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        base = self.title or self.get_document_type_display()
        return f"{self.patient_id} - {base}"
