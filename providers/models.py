from django.conf import settings
from django.core.validators import RegexValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from facilities.models import Specialty  # reuse seeded specialties
from .enums import ProviderType, Council, VerificationStatus

phone_validator = RegexValidator(
    regex=r"^\+\d{1,3}\d{6,14}$",
    message="Phone must be E.164 format (e.g. +2348012345678).",
)

class ProviderProfile(models.Model):
    """
    Independent provider profile (not tied to a Facility).
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="provider_profile")

    provider_type = models.CharField(max_length=24, choices=ProviderType.choices, default=ProviderType.DOCTOR)
    specialties = models.ManyToManyField(Specialty, blank=True, related_name="providers")

    # Identity & licensing
    license_council = models.CharField(max_length=16, choices=Council.choices, default=Council.MDCN)
    license_number = models.CharField(max_length=64)
    license_expiry = models.DateField(null=True, blank=True)

    years_experience = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    bio = models.TextField(blank=True)

    # Contact & address
    phone = models.CharField(max_length=20, validators=[phone_validator], blank=True)
    country = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    lga = models.CharField(max_length=120, blank=True)
    address = models.TextField(blank=True)

    # Business
    consultation_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Verification workflow
    verification_status = models.CharField(max_length=16, choices=VerificationStatus.choices, default=VerificationStatus.PENDING)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="providers_verified")
    rejection_reason = models.CharField(max_length=255, blank=True)

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def approve(self, by_user):
        self.verification_status = VerificationStatus.APPROVED
        self.verified_by = by_user
        self.verified_at = timezone.now()
        self.rejection_reason = ""
        self.save(update_fields=["verification_status","verified_by","verified_at","rejection_reason","updated_at"])

    def reject(self, by_user, reason=""):
        self.verification_status = VerificationStatus.REJECTED
        self.verified_by = by_user
        self.verified_at = timezone.now()
        self.rejection_reason = reason[:255]
        self.save(update_fields=["verification_status","verified_by","verified_at","rejection_reason","updated_at"])

    def __str__(self):
        return f"{self.user.email} ({self.get_provider_type_display()})"

class ProviderDocument(models.Model):
    """
    Optional simple document store for provider uploads (ID, license scans).
    Prefer using attachments app for general storage; this is a convenience.
    """
    profile = models.ForeignKey(ProviderProfile, on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField(max_length=64, blank=True)  # e.g., "LICENSE","ID","CERT"
    file = models.FileField(upload_to="provider_docs/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
