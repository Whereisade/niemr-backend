from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.core.validators import RegexValidator, MinValueValidator
from django.db import models
from facilities.models import Facility
from .enums import (PatientStatus, EncounterType, BloodGroup, Genotype, InsuranceStatus)

phone_validator = RegexValidator(
    regex=r"^\+\d{1,3}\d{6,14}$",
    message="Phone must be E.164 format (e.g. +2348012345678).",
)

class HMO(models.Model):
    name = models.CharField(max_length=160, unique=True)
    def __str__(self): return self.name

class Patient(models.Model):
    # ownership / scoping
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    facility = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.SET_NULL, related_name="patients")
    guardian_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="dependents")  # parent/guardian

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

    def save(self, *args, **kwargs):
        self.bmi = self._calc_bmi()
        # normalize OTHER fields
        if self.blood_group != BloodGroup.OTHER:
            self.blood_group_other = ""
        if self.genotype != Genotype.OTHER:
            self.genotype_other = ""
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.last_name}, {self.first_name}"
    
class PatientDocument(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=64, blank=True)  # e.g., Blood test/Xray/Ultrasound/Discharge/Referral/Lab/CT
    file = models.FileField(upload_to="patient_docs/")
    uploaded_by_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_at = models.DateTimeField(auto_now_add=True)
