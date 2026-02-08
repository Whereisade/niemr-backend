from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
import uuid
from django.core.validators import RegexValidator, MinValueValidator
from django.db import models
from django.core.exceptions import ValidationError
from facilities.models import Facility
from .enums import (
    PatientStatus, EncounterType, BloodGroup, Genotype, InsuranceStatus,
    AllergyType, AllergySeverity
)
from django.utils import timezone
phone_validator = RegexValidator(
    regex=r"^\+\d{1,3}\d{6,14}$",
    message="Phone must be E.164 format (e.g. +2348012345678).",
)

class HMO(models.Model):
    """
    Facility-scoped HMO with enhanced contact details.

    Facilities (via their SUPER_ADMIN user) create HMOs. Patients can then be
    attached to an HMO within the same facility. Pricing overrides for a given
    HMO are stored in billing.HMOPrice (per service code).
    """
    
    class RelationshipStatus(models.TextChoices):
        EXCELLENT = "EXCELLENT", "Excellent"
        GOOD = "GOOD", "Good"
        FAIR = "FAIR", "Fair"
        POOR = "POOR", "Poor"
        BAD = "BAD", "Bad"
    
    facility = models.ForeignKey(
        Facility,
        on_delete=models.CASCADE,
        related_name="hmos",
        null=True,
        blank=True,
    )
    
    # Basic Information
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    
    # NHIS Registration
    nhis_number = models.CharField(
        max_length=120,
        blank=True,
        default='',
        help_text='NHIS registration number'
    )
    
    # Contact Information
    email = models.EmailField(
        max_length=254,
        blank=True,
        help_text='HMO primary email address'
    )
    
    # Multiple addresses and phone numbers (stored as JSON)
    addresses = models.JSONField(
        default=list,
        blank=True,
        help_text='List of HMO office addresses'
    )
    
    contact_numbers = models.JSONField(
        default=list,
        blank=True,
        help_text='List of contact phone numbers'
    )
    
    # Contact Person Details
    contact_person_name = models.CharField(
        max_length=255,
        blank=True,
        help_text='Name of HMO contact person'
    )
    
    contact_person_phone = models.CharField(
        max_length=20,
        blank=True,
        help_text='Contact person phone number'
    )
    
    contact_person_email = models.EmailField(
        max_length=254,
        blank=True,
        help_text='Contact person email address'
    )
    
    # 🆕 Relationship Status Fields
    relationship_status = models.CharField(
        max_length=20,
        choices=RelationshipStatus.choices,
        default=RelationshipStatus.GOOD,
        blank=True,
        help_text='Current relationship status with this HMO'
    )
    
    relationship_notes = models.TextField(
        blank=True,
        help_text='Notes about the relationship status'
    )
    
    relationship_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the relationship status was last updated'
    )
    
    relationship_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='hmo_relationship_updates'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["facility", "name"],
                name="uniq_hmo_name_per_facility",
            )
        ]

    def __str__(self):
        if self.facility_id:
            return f"{self.name} ({self.facility.name})"
        return self.name
    
    def get_primary_address(self):
        """Return the first address or empty string"""
        if self.addresses and len(self.addresses) > 0:
            return self.addresses[0]
        return ""
    
    def get_primary_contact(self):
        """Return the first contact number or empty string"""
        if self.contact_numbers and len(self.contact_numbers) > 0:
            return self.contact_numbers[0]
        return ""
    
    def get_relationship_status_color(self):
        """Return color code for relationship status"""
        colors = {
            self.RelationshipStatus.EXCELLENT: "emerald",
            self.RelationshipStatus.GOOD: "blue",
            self.RelationshipStatus.FAIR: "yellow",
            self.RelationshipStatus.POOR: "orange",
            self.RelationshipStatus.BAD: "red",
        }
        return colors.get(self.relationship_status, "slate")


# ============================================================================
# SYSTEM-SCOPED HMO MODELS
# ============================================================================

class SystemHMO(models.Model):
    """
    Master list of HMOs at the system level.
    
    These are seeded by administrators and shared across all facilities.
    Facilities "enable" these HMOs via FacilityHMO to work with them.
    """
    
    name = models.CharField(
        max_length=255,
        unique=True,
        help_text="Official HMO name (e.g., 'Leadway Health', 'NHIS')"
    )
    
    # Registration & identification
    nhis_number = models.CharField(
        max_length=120,
        blank=True,
        default='',
        help_text='NHIS registration number'
    )
    
    # Contact Information
    email = models.EmailField(
        max_length=254,
        blank=True,
        help_text='HMO primary email address'
    )
    
    # Multiple addresses and phone numbers (stored as JSON)
    addresses = models.JSONField(
        default=list,
        blank=True,
        help_text='List of HMO office addresses'
    )
    
    contact_numbers = models.JSONField(
        default=list,
        blank=True,
        help_text='List of contact phone numbers'
    )
    
    # Contact Person Details
    contact_person_name = models.CharField(
        max_length=255,
        blank=True,
        help_text='Name of HMO contact person'
    )
    
    contact_person_phone = models.CharField(
        max_length=20,
        blank=True,
        help_text='Contact person phone number'
    )
    
    contact_person_email = models.EmailField(
        max_length=254,
        blank=True,
        help_text='Contact person email address'
    )
    
    # Website and additional info
    website = models.URLField(
        max_length=255,
        blank=True,
        help_text='HMO website URL'
    )
    
    description = models.TextField(
        blank=True,
        help_text='Additional information about this HMO'
    )
    
    # Status
    is_active = models.BooleanField(
        default=True,
        help_text='Whether this HMO is available for facility enrollment'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
        verbose_name = 'System HMO'
        verbose_name_plural = 'System HMOs'
    
    def __str__(self):
        return self.name
    
    def get_primary_address(self):
        """Return the first address or empty string"""
        if self.addresses and len(self.addresses) > 0:
            return self.addresses[0]
        return ""
    
    def get_primary_contact(self):
        """Return the first contact number or empty string"""
        if self.contact_numbers and len(self.contact_numbers) > 0:
            return self.contact_numbers[0]
        return ""
    
    def save(self, *args, **kwargs):
        """Auto-create default tiers when a new SystemHMO is created."""
        is_new = self.pk is None
        super().save(*args, **kwargs)
        
        if is_new:
            self._create_default_tiers()
    
    def _create_default_tiers(self):
        """Create the 3 default tiers for this HMO."""
        default_tiers = [
            {
                'name': 'Gold',
                'level': 1,
                'description': 'Premium tier with highest coverage and benefits'
            },
            {
                'name': 'Silver',
                'level': 2,
                'description': 'Standard tier with good coverage and benefits'
            },
            {
                'name': 'Bronze',
                'level': 3,
                'description': 'Basic tier with essential coverage'
            },
        ]
        
        for tier_data in default_tiers:
            HMOTier.objects.get_or_create(
                system_hmo=self,
                level=tier_data['level'],
                defaults={
                    'name': tier_data['name'],
                    'description': tier_data['description'],
                }
            )


class HMOTier(models.Model):
    """
    Predefined tiers for each SystemHMO.
    
    Every HMO has exactly 3 tiers: Gold (1), Silver (2), Bronze (3).
    These are auto-created when a SystemHMO is created.
    """
    
    system_hmo = models.ForeignKey(
        SystemHMO,
        on_delete=models.CASCADE,
        related_name='tiers'
    )
    
    name = models.CharField(
        max_length=64,
        help_text='Tier name (Gold, Silver, Bronze)'
    )
    
    level = models.PositiveIntegerField(
        help_text='Tier level for ordering (1=Gold/highest, 2=Silver, 3=Bronze/lowest)'
    )
    
    description = models.TextField(
        blank=True,
        help_text='Description of tier benefits and coverage'
    )
    
    is_active = models.BooleanField(
        default=True,
        help_text='Whether this tier is available for patient enrollment'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['system_hmo', 'level']
        unique_together = ('system_hmo', 'level')
        verbose_name = 'HMO Tier'
        verbose_name_plural = 'HMO Tiers'
    
    def __str__(self):
        return f"{self.system_hmo.name} - {self.name}"
    
    @property
    def tier_display(self):
        """Friendly display name with HMO"""
        return f"{self.name} ({self.system_hmo.name})"


class FacilityHMO(models.Model):
    """
    Junction table representing a facility's or independent provider's
    relationship with a SystemHMO.
    
    This replaces the old facility-scoped HMO model.
    
    Rules:
    - Either facility OR owner must be set (not both, not neither)
    - Unique per facility/owner + system_hmo combination
    """
    
    class RelationshipStatus(models.TextChoices):
        EXCELLENT = "EXCELLENT", "Excellent"
        GOOD = "GOOD", "Good"
        FAIR = "FAIR", "Fair"
        POOR = "POOR", "Poor"
        BAD = "BAD", "Bad"
    
    # Scope: either facility or independent provider (owner)
    facility = models.ForeignKey(
        'facilities.Facility',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='facility_hmos',
        help_text='Facility that enabled this HMO'
    )
    
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='provider_hmos',
        help_text='Independent provider who enabled this HMO'
    )
    
    # The system-level HMO
    system_hmo = models.ForeignKey(
        SystemHMO,
        on_delete=models.CASCADE,
        related_name='facility_links'
    )
    
    # Relationship tracking
    relationship_status = models.CharField(
        max_length=20,
        choices=RelationshipStatus.choices,
        default=RelationshipStatus.GOOD,
        blank=True,
        help_text='Current relationship status with this HMO'
    )
    
    relationship_notes = models.TextField(
        blank=True,
        help_text='Notes about the relationship (payment history, issues, etc.)'
    )
    
    relationship_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the relationship status was last updated'
    )
    
    relationship_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='facility_hmo_relationship_updates'
    )
    
    # Contract/Agreement details (optional)
    contract_start_date = models.DateField(
        null=True,
        blank=True,
        help_text='Start date of contract with this HMO'
    )
    
    contract_end_date = models.DateField(
        null=True,
        blank=True,
        help_text='End date of contract with this HMO'
    )
    
    contract_reference = models.CharField(
        max_length=120,
        blank=True,
        help_text='Contract reference number'
    )
    
    # ========================================================================
    # FACILITY-SPECIFIC CONTACT INFORMATION
    # ========================================================================
    # These fields override the SystemHMO contact info for this specific facility
    # This allows each facility to have their own local HMO office contact details
    
    email = models.EmailField(
        max_length=254,
        blank=True,
        default='',
        help_text='Facility-specific HMO email address'
    )
    
    addresses = models.JSONField(
        default=list,
        blank=True,
        help_text='Facility-specific HMO office addresses (JSON array)'
    )
    
    contact_numbers = models.JSONField(
        default=list,
        blank=True,
        help_text='Facility-specific contact phone numbers (JSON array)'
    )
    
    contact_person_name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text='Name of facility-specific HMO contact person'
    )
    
    contact_person_phone = models.CharField(
        max_length=20,
        blank=True,
        default='',
        help_text='Facility-specific contact person phone number'
    )
    
    contact_person_email = models.EmailField(
        max_length=254,
        blank=True,
        default='',
        help_text='Facility-specific contact person email address'
    )
    
    nhis_number = models.CharField(
        max_length=120,
        blank=True,
        default='',
        help_text='Facility-specific NHIS number (if different from system HMO)'
    )
    # ========================================================================
    
    # Status
    is_active = models.BooleanField(
        default=True,
        help_text='Whether this facility currently works with this HMO'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['system_hmo__name']
        verbose_name = 'Facility HMO'
        verbose_name_plural = 'Facility HMOs'
        constraints = [
            # Either facility OR owner must be set (XOR)
            models.CheckConstraint(
                name='facility_hmo_scope_xor',
                check=(
                    (models.Q(facility__isnull=False) & models.Q(owner__isnull=True))
                    | (models.Q(facility__isnull=True) & models.Q(owner__isnull=False))
                ),
            ),
            # Unique per facility + system_hmo
            models.UniqueConstraint(
                name='unique_facility_system_hmo',
                fields=['facility', 'system_hmo'],
                condition=models.Q(facility__isnull=False),
            ),
            # Unique per owner + system_hmo
            models.UniqueConstraint(
                name='unique_owner_system_hmo',
                fields=['owner', 'system_hmo'],
                condition=models.Q(owner__isnull=False),
            ),
        ]
    
    def __str__(self):
        scope = self.facility.name if self.facility else f"Provider:{self.owner_id}"
        return f"{scope} - {self.system_hmo.name}"
    
    def get_scope_name(self):
        """Return the name of the facility or provider"""
        if self.facility:
            return self.facility.name
        if self.owner:
            return f"{self.owner.first_name} {self.owner.last_name}".strip() or self.owner.email
        return "Unknown"
    
    def get_relationship_status_color(self):
        """Return color code for relationship status"""
        colors = {
            self.RelationshipStatus.EXCELLENT: "emerald",
            self.RelationshipStatus.GOOD: "blue",
            self.RelationshipStatus.FAIR: "yellow",
            self.RelationshipStatus.POOR: "orange",
            self.RelationshipStatus.BAD: "red",
        }
        return colors.get(self.relationship_status, "slate")
    
    def get_primary_address(self):
        """Return the first address or empty string"""
        if self.addresses and len(self.addresses) > 0:
            return self.addresses[0]
        return ""
    
    def get_primary_contact(self):
        """Return the first contact number or empty string"""
        if self.contact_numbers and len(self.contact_numbers) > 0:
            return self.contact_numbers[0]
        return ""


class PatientFacilityHMOApproval(models.Model):
    """
    Tracks HMO approval status when a patient transfers between facilities.
    
    When a patient with an existing HMO enrollment registers at a new facility:
    1. An approval record is created with PENDING status
    2. Facility admin reviews and approves/rejects
    3. If approved, patient can use their HMO at this facility
    
    This enables the "transfer approval" workflow.
    """
    
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending Review'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'
    
    # The patient requesting approval
    patient = models.ForeignKey(
        'patients.Patient',
        on_delete=models.CASCADE,
        related_name='hmo_facility_approvals'
    )
    
    # Where the approval is needed (facility or independent provider)
    facility = models.ForeignKey(
        'facilities.Facility',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='patient_hmo_approvals'
    )
    
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='provider_patient_hmo_approvals',
        help_text='Independent provider reviewing the approval'
    )
    
    # The HMO and tier being requested
    system_hmo = models.ForeignKey(
        SystemHMO,
        on_delete=models.CASCADE,
        related_name='patient_approvals'
    )
    
    tier = models.ForeignKey(
        HMOTier,
        on_delete=models.CASCADE,
        related_name='patient_approvals'
    )
    
    # Insurance details (copied from patient's enrollment)
    insurance_number = models.CharField(
        max_length=120,
        blank=True,
        help_text='Insurance card number'
    )
    
    insurance_expiry = models.DateField(
        null=True,
        blank=True,
        help_text='Insurance expiry date'
    )
    
    # Where the patient was originally enrolled
    original_facility = models.ForeignKey(
        'facilities.Facility',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='Facility where patient originally enrolled with this HMO'
    )
    
    original_provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='Independent provider where patient originally enrolled'
    )
    
    # Approval status
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING
    )
    
    # Decision tracking
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='hmo_approval_decisions'
    )
    
    # Notes
    request_notes = models.TextField(
        blank=True,
        help_text='Notes from the patient or referring facility'
    )
    
    decision_notes = models.TextField(
        blank=True,
        help_text='Notes from the approving facility/provider'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-requested_at']
        verbose_name = 'Patient Facility HMO Approval'
        verbose_name_plural = 'Patient Facility HMO Approvals'
        constraints = [
            # Either facility OR owner must be set (XOR)
            models.CheckConstraint(
                name='patient_hmo_approval_scope_xor',
                check=(
                    (models.Q(facility__isnull=False) & models.Q(owner__isnull=True))
                    | (models.Q(facility__isnull=True) & models.Q(owner__isnull=False))
                ),
            ),
            # Only one pending approval per patient per facility/owner
            models.UniqueConstraint(
                name='unique_pending_patient_facility_hmo_approval',
                fields=['patient', 'facility'],
                condition=models.Q(status='PENDING', facility__isnull=False),
            ),
            models.UniqueConstraint(
                name='unique_pending_patient_owner_hmo_approval',
                fields=['patient', 'owner'],
                condition=models.Q(status='PENDING', owner__isnull=False),
            ),
        ]
    
    def __str__(self):
        scope = self.facility.name if self.facility else f"Provider:{self.owner_id}"
        return f"{self.patient} - {self.system_hmo.name} @ {scope} ({self.status})"
    
    def approve(self, by_user, notes=''):
        """Approve the HMO enrollment for this facility."""
        self.status = self.Status.APPROVED
        self.decided_at = timezone.now()
        self.decided_by = by_user
        self.decision_notes = notes
        self.save(update_fields=[
            'status', 'decided_at', 'decided_by', 'decision_notes', 'updated_at'
        ])
        
        # Update patient's facility-specific HMO approval
        self._update_patient_hmo_status(approved=True)
    
    def reject(self, by_user, notes=''):
        """Reject the HMO enrollment for this facility."""
        self.status = self.Status.REJECTED
        self.decided_at = timezone.now()
        self.decided_by = by_user
        self.decision_notes = notes
        self.save(update_fields=[
            'status', 'decided_at', 'decided_by', 'decision_notes', 'updated_at'
        ])
        
        self._update_patient_hmo_status(approved=False)
    
    def _update_patient_hmo_status(self, approved: bool):
        """
        Update patient's HMO status at this facility.
        If approved, patient can use HMO. If rejected, patient is self-pay at this facility.
        """
        # This would update the patient's active HMO status for this facility
        # Implementation depends on how you want to track per-facility HMO status
        pass


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
    insurance_number = models.CharField(max_length=120, blank=True, help_text="Insurance card number or policy ID")
    insurance_expiry = models.DateField(null=True, blank=True, help_text="Insurance coverage expiry date")
    insurance_notes = models.TextField(blank=True, help_text="Additional insurance information")
    
    # System-scoped HMO enrollment (NEW)
    system_hmo = models.ForeignKey(
        'patients.SystemHMO',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='patients',
        help_text='System-level HMO the patient is enrolled with'
    )
    
    hmo_tier = models.ForeignKey(
        'patients.HMOTier',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='patients',
        help_text='The tier level of HMO coverage'
    )
    
    # Track where patient was first enrolled with this HMO
    hmo_enrollment_facility = models.ForeignKey(
        'facilities.Facility',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='hmo_enrolled_patients',
        help_text='Facility where patient first enrolled with current HMO'
    )
    
    hmo_enrollment_provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='hmo_enrolled_patients',
        help_text='Independent provider where patient first enrolled with current HMO'
    )
    
    hmo_enrolled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When patient enrolled with current HMO'
    )

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
    def full_name(self) -> str:
        """Human-friendly patient name.

        Used across notifications, dashboards, and documents.
        Returns 'First Middle Last' (skips empty parts).
        """
        parts = [getattr(self, 'first_name', ''), getattr(self, 'middle_name', ''), getattr(self, 'last_name', '')]
        return ' '.join([p for p in parts if p]).strip()

    @property
    def is_dependent(self) -> bool:
        """True if this patient is a dependent of another patient."""
        return bool(self.parent_patient_id)

    def __str__(self):
        return f"{self.last_name}, {self.first_name}"


class PatientProviderLink(models.Model):
    """Link table for *independent* providers (no facility) to manage a patient roster.

    Facility staff already scope patients by `Patient.facility`.
    Independent providers do not have a facility, so we attach patients to them
    explicitly via this link.
    """

    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="provider_links",
    )
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="managed_patients_links",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("patient", "provider")
        indexes = [
            models.Index(fields=["provider", "patient"]),
            models.Index(fields=["patient", "provider"]),
        ]

    def __str__(self):
        return f"PatientProviderLink(patient={self.patient_id}, provider={self.provider_id})"
    


class PatientFacilityLink(models.Model):
    """Permanent link between a Patient and a Facility they have visited.

    Why:
      - A patient can visit multiple facilities over time.
      - Each facility should keep the patient in its records permanently.
    """

    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="facility_links",
    )
    facility = models.ForeignKey(
        Facility,
        on_delete=models.CASCADE,
        related_name="patient_links",
    )
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("patient", "facility")
        indexes = [
            models.Index(fields=["facility", "patient"]),
            models.Index(fields=["patient", "facility"]),
        ]

    def __str__(self):
        return f"PatientFacilityLink(patient={self.patient_id}, facility={self.facility_id})"


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
        default=UploadedBy.PATIENT,  # docs tagged "Uploaded by Patient" by default
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


class Allergy(models.Model):
    """
    Patient allergy record.
    
    Tracks allergies reported by patients or recorded by healthcare providers.
    Critical for medication safety and clinical decision support.
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="allergies",
    )
    
    # What the patient is allergic to
    allergen = models.CharField(
        max_length=255,
        help_text="The substance the patient is allergic to (e.g., Penicillin, Peanuts)"
    )
    
    # Type/category of allergy
    allergy_type = models.CharField(
        max_length=20,
        choices=AllergyType.choices,
        default=AllergyType.OTHER,
    )
    
    # How severe is the reaction
    severity = models.CharField(
        max_length=20,
        choices=AllergySeverity.choices,
        default=AllergySeverity.MODERATE,
    )
    
    # What happens when exposed
    reaction = models.TextField(
        blank=True,
        help_text="Description of the allergic reaction (e.g., rash, anaphylaxis)"
    )
    
    # When did the allergy start/was discovered
    onset_date = models.DateField(
        null=True,
        blank=True,
        help_text="When the allergy was first identified or occurred"
    )
    
    # Additional notes
    notes = models.TextField(blank=True)
    
    # Who recorded this allergy
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_allergies",
    )
    
    # Is this allergy still active/relevant
    is_active = models.BooleanField(default=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ("-created_at",)
        verbose_name_plural = "Allergies"
        # Prevent duplicate allergens for the same patient
        constraints = [
            models.UniqueConstraint(
                fields=["patient", "allergen"],
                name="unique_patient_allergen",
                condition=models.Q(is_active=True),
            )
        ]
    
    def __str__(self):
        return f"{self.patient} - {self.allergen} ({self.get_severity_display()})"
