from django.db import models

# Create your models here.
import re
from django.core.validators import RegexValidator, EmailValidator
from django.db import models
from .enums import FacilityType

phone_validator = RegexValidator(
    regex=r"^\+\d{1,3}\d{6,14}$",
    message="Phone must be E.164 format (e.g. +2348012345678).",
)

class Specialty(models.Model):
    name = models.CharField(max_length=120, unique=True)

    def __str__(self): return self.name

class Facility(models.Model):
    # Registration fields
    facility_type = models.CharField(max_length=32, choices=FacilityType.choices, default=FacilityType.HOSPITAL)
    name = models.CharField(max_length=255)
    controlled_by = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=120)
    state = models.CharField(max_length=120)
    lga = models.CharField(max_length=120, verbose_name="Local Govt Area")
    address = models.TextField(blank=True)

    email = models.EmailField(validators=[EmailValidator()])
    registration_number = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, validators=[phone_validator], help_text="E.164 format")

    nhis_approved = models.BooleanField(default=False)
    nhis_number = models.CharField(max_length=120, blank=True)

    # Profile completion
    total_bed_capacity = models.PositiveIntegerField(default=0)
    specialties = models.ManyToManyField(Specialty, blank=True, related_name="facilities")

    # Status / audit
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Document uploads
    nhis_certificate = models.FileField(upload_to="facility_docs/", blank=True)
    md_practice_license = models.FileField(upload_to="facility_docs/", blank=True)
    state_registration_cert = models.FileField(upload_to="facility_docs/", blank=True)

    def __str__(self): return f"{self.name} ({self.get_facility_type_display()})"

class FacilityExtraDocument(models.Model):
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="extra_docs")
    title = models.CharField(max_length=120, default="Other")
    file = models.FileField(upload_to="facility_docs/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

class Ward(models.Model):
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="wards")
    name = models.CharField(max_length=120)
    capacity = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("facility","name")

    def __str__(self): return f"{self.facility.name} - {self.name}"

class Bed(models.Model):
    ward = models.ForeignKey(Ward, on_delete=models.CASCADE, related_name="beds")
    number = models.CharField(max_length=16)  # free-format (e.g., A01)
    is_available = models.BooleanField(default=True)

    class Meta:
        unique_together = ("ward","number")

    def __str__(self): return f"{self.ward.name} / {self.number}"
