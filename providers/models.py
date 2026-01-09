from django.conf import settings
from django.core.validators import RegexValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from facilities.models import Specialty, Facility
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
    
    # Business information
    business_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Business/practice name for independent providers (e.g., 'City Medical Lab', 'Downtown Pharmacy')"
    )

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

    def get_display_name(self):
        """
        Get the display name for this provider.
        Priority: business_name > full_name > email
        """
        if self.business_name:
            return self.business_name.strip()
        
        user = self.user
        if user:
            # Try to get full name
            if hasattr(user, "get_full_name"):
                full_name = (user.get_full_name() or "").strip()
            else:
                first = getattr(user, "first_name", "") or ""
                last = getattr(user, "last_name", "") or ""
                full_name = f"{first} {last}".strip()
            
            if full_name:
                return full_name
            
            # Fallback to email
            if hasattr(user, "email"):
                return user.email
        
        return f"Provider #{self.id}"

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

class ProviderFacilityApplication(models.Model):
    """
    A provider's request to join a Facility.

    Created by PROVIDER accounts, reviewed by Facility admins.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")

    provider = models.ForeignKey(
        "providers.ProviderProfile",
        on_delete=models.CASCADE,
        related_name="facility_applications",
    )
    facility = models.ForeignKey(
        "facilities.Facility",
        on_delete=models.CASCADE,
        related_name="provider_applications",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name="provider_facility_decisions",
        on_delete=models.SET_NULL,
    )

    class Meta:
        unique_together = ("provider", "facility")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.provider} → {self.facility} ({self.status})"