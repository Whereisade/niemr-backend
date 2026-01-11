from django.db import models
from django.conf import settings
from django.utils import timezone
from accounts.enums import UserRole
# Create your models here.
import re
from django.core.validators import RegexValidator, EmailValidator
from .enums import FacilityType

phone_validator = RegexValidator(
    regex=r"^\+\d{1,3}\d{6,14}$",
    message="Phone must be E.164 format (e.g. +2348012345678).",
)


class Specialty(models.Model):
    name = models.CharField(max_length=120, unique=True)

    def __str__(self):
        return self.name


class Facility(models.Model):
    # Registration fields
    facility_type = models.CharField(
        max_length=32,
        choices=FacilityType.choices,
        default=FacilityType.HOSPITAL,
    )

    CONTROLLED_BY_CHOICES = [
        ("federal", "Federal"),
        ("state", "State"),
        ("church", "Church"),
        ("ngo", "NGO"),
        ("private", "Private"),
    ]

    COUNTRY_CHOICES = [
        ("nigeria", "Nigeria"),
        ("ghana", "Ghana"),
        ("kenya", "Kenya"),
        ("south_africa", "South Africa"),
    ]

    # Nigerian states
    NIGERIA_STATES = [
        ("abia", "Abia"),
        ("adamawa", "Adamawa"),
        ("akwa_ibom", "Akwa Ibom"),
        ("anambra", "Anambra"),
        ("bauchi", "Bauchi"),
        ("bayelsa", "Bayelsa"),
        ("benue", "Benue"),
        ("borno", "Borno"),
        ("cross_river", "Cross River"),
        ("delta", "Delta"),
        ("ebonyi", "Ebonyi"),
        ("edo", "Edo"),
        ("ekiti", "Ekiti"),
        ("enugu", "Enugu"),
        ("gombe", "Gombe"),
        ("imo", "Imo"),
        ("jigawa", "Jigawa"),
        ("kaduna", "Kaduna"),
        ("kano", "Kano"),
        ("katsina", "Katsina"),
        ("kebbi", "Kebbi"),
        ("kogi", "Kogi"),
        ("kwara", "Kwara"),
        ("lagos", "Lagos"),
        ("nasarawa", "Nasarawa"),
        ("niger", "Niger"),
        ("ogun", "Ogun"),
        ("ondo", "Ondo"),
        ("osun", "Osun"),
        ("oyo", "Oyo"),
        ("plateau", "Plateau"),
        ("rivers", "Rivers"),
        ("sokoto", "Sokoto"),
        ("taraba", "Taraba"),
        ("yobe", "Yobe"),
        ("zamfara", "Zamfara"),
        ("fct", "FCT - Abuja"),
    ]

    # Ghana regions
    GHANA_REGIONS = [
        ("ashanti", "Ashanti"),
        ("brong_ahafo", "Brong-Ahafo"),
        ("central", "Central"),
        ("eastern", "Eastern"),
        ("greater_accra", "Greater Accra"),
        ("northern", "Northern"),
        ("savannah", "Savannah"),
        ("upper_east", "Upper East"),
        ("upper_west", "Upper West"),
        ("volta", "Volta"),
        ("western", "Western"),
        ("western_north", "Western North"),
    ]

    # Kenya counties
    KENYA_COUNTIES = [
        ("nairobi", "Nairobi"),
        ("mombasa", "Mombasa"),
        ("kisumu", "Kisumu"),
        ("nakuru", "Nakuru"),
        ("eldoret", "Eldoret"),
        ("machakos", "Machakos"),
        ("nyeri", "Nyeri"),
        ("kisii", "Kisii"),
        ("muranga", "Murang'a"),
        ("garissa", "Garissa"),
        ("lamu", "Lamu"),
        ("marsabit", "Marsabit"),
        ("kilifi", "Kilifi"),
        ("bomet", "Bomet"),
        ("turkana", "Turkana"),
    ]

    # South Africa provinces
    SOUTH_AFRICA_PROVINCES = [
        ("eastern_cape", "Eastern Cape"),
        ("free_state", "Free State"),
        ("gauteng", "Gauteng"),
        ("kwazulu_natal", "KwaZulu-Natal"),
        ("limpopo", "Limpopo"),
        ("mpumalanga", "Mpumalanga"),
        ("northern_cape", "Northern Cape"),
        ("north_west", "North West"),
        ("western_cape", "Western Cape"),
    ]

    # Combine all options into one big STATE_CHOICES list
    STATE_CHOICES = (
        [("---nigeria---", "--- Nigeria ---")]
        + NIGERIA_STATES
        + [("---ghana---", "--- Ghana ---")]
        + GHANA_REGIONS
        + [("---kenya---", "--- Kenya ---")]
        + KENYA_COUNTIES
        + [("---south_africa---", "--- South Africa ---")]
        + SOUTH_AFRICA_PROVINCES
    )

    name = models.CharField(max_length=255)
    controlled_by = models.CharField(
        max_length=20,
        choices=CONTROLLED_BY_CHOICES,
        default="private",
    )
    country = models.CharField(
        max_length=50,
        choices=COUNTRY_CHOICES,
        default="nigeria",
    )
    state = models.CharField(
        max_length=50,
        choices=STATE_CHOICES,
        blank=True,
        null=True,
    )
    lga = models.CharField(max_length=120, verbose_name="Local Govt Area")
    address = models.TextField(blank=True)

    email = models.EmailField(validators=[EmailValidator()])
    registration_number = models.CharField(max_length=120, blank=True)
    phone = models.CharField(
        max_length=20,
        validators=[phone_validator],
        help_text="E.164 format",
    )

    nhis_approved = models.BooleanField(default=False)
    nhis_number = models.CharField(max_length=120, blank=True)

    # Profile completion
    total_bed_capacity = models.PositiveIntegerField(default=0)
    specialties = models.ManyToManyField(
        Specialty,
        blank=True,
        related_name="facilities",
    )

    # Status / audit
    is_active = models.BooleanField(default=True)
    is_publicly_visible = models.BooleanField(
    default=True,
    help_text="Controls whether this facility appears in public search and can accept online bookings"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Document uploads
    nhis_certificate = models.FileField(
        upload_to="facility_docs/",
        blank=True,
    )
    md_practice_license = models.FileField(
        upload_to="facility_docs/",
        blank=True,
    )
    state_registration_cert = models.FileField(
        upload_to="facility_docs/",
        blank=True,
    )

    def __str__(self):
        return f"{self.name} ({self.get_facility_type_display()})"


class FacilityExtraDocument(models.Model):
    facility = models.ForeignKey(
        Facility,
        on_delete=models.CASCADE,
        related_name="extra_docs",
    )
    title = models.CharField(max_length=120, default="Other")
    file = models.FileField(upload_to="facility_docs/")
    uploaded_at = models.DateTimeField(auto_now_add=True)


class Ward(models.Model):
    class WardType(models.TextChoices):
        GENERAL = "GENERAL", "General"
        ICU = "ICU", "ICU"
        PICU = "PICU", "Pediatric ICU"
        NICU = "NICU", "Neonatal ICU"
        MATERNITY = "MATERNITY", "Maternity"
        ISOLATION = "ISOLATION", "Isolation"

    class GenderPolicy(models.TextChoices):
        MIXED = "MIXED", "Mixed"
        MALE_ONLY = "MALE_ONLY", "Male only"
        FEMALE_ONLY = "FEMALE_ONLY", "Female only"

    facility = models.ForeignKey(
        "facilities.Facility",
        related_name="wards",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=120)
    capacity = models.PositiveIntegerField(default=0)

    # ✅ NEW FIELDS
    ward_type = models.CharField(
        max_length=32,
        choices=WardType.choices,
        default=WardType.GENERAL,
    )
    gender_policy = models.CharField(
        max_length=32,
        choices=GenderPolicy.choices,
        default=GenderPolicy.MIXED,
    )
    floor = models.CharField(
        max_length=64,
        blank=True,
        help_text="Optional floor or block label (e.g. 'Ground', '1st floor').",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("facility", "name")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_ward_type_display()})"


class Bed(models.Model):
    class BedClass(models.TextChoices):
        GENERAL = "GENERAL", "General"
        SEMI_PRIVATE = "SEMI_PRIVATE", "Semi-private"
        PRIVATE = "PRIVATE", "Private"

    class BedStatus(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        OCCUPIED = "OCCUPIED", "Occupied"
        CLEANING = "CLEANING", "Cleaning"
        OUT_OF_SERVICE = "OUT_OF_SERVICE", "Out of service"

    ward = models.ForeignKey(
        Ward,
        related_name="beds",
        on_delete=models.CASCADE,
    )
    number = models.CharField(max_length=20)

    # ✅ NEW FIELDS
    bed_class = models.CharField(
        max_length=32,
        choices=BedClass.choices,
        default=BedClass.GENERAL,
    )
    status = models.CharField(
        max_length=32,
        choices=BedStatus.choices,
        default=BedStatus.AVAILABLE,
    )
    has_oxygen = models.BooleanField(default=False)
    has_monitor = models.BooleanField(default=False)
    is_operational = models.BooleanField(
        default=True,
        help_text="Set to false if this bed cannot be used (faulty, blocked, etc).",
    )
    notes = models.TextField(blank=True)

    # ⏳ Backwards-compat with existing code
    is_available = models.BooleanField(default=True)

    class Meta:
        unique_together = ("ward", "number")
        ordering = ["ward__name", "number"]

    def __str__(self):
        return f"{self.ward.name} - {self.number}"


def refresh_bed_status(bed: "Bed"):
    """
    Set bed.status / is_available based on whether there is an active assignment.
    """
    has_active = bed.assignments.filter(discharged_at__isnull=True).exists()

    if has_active:
        if hasattr(bed, "status"):
            bed.status = Bed.BedStatus.OCCUPIED
        bed.is_available = False
    else:
        # Only flip back to available if bed is otherwise usable
        if getattr(bed, "is_operational", True) and getattr(
            bed, "status", None
        ) != getattr(Bed, "BedStatus", None).OUT_OF_SERVICE:
            if hasattr(bed, "status"):
                bed.status = Bed.BedStatus.AVAILABLE
            bed.is_available = True

    bed.save(update_fields=["status", "is_available"])


class BedAssignment(models.Model):
    bed = models.ForeignKey(
        "facilities.Bed",
        related_name="assignments",
        on_delete=models.PROTECT,
    )
    patient = models.ForeignKey(
        "patients.Patient",
        related_name="bed_assignments",
        on_delete=models.PROTECT,
    )
    encounter = models.ForeignKey(
        "encounters.Encounter",
        related_name="bed_assignments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    assigned_at = models.DateTimeField(default=timezone.now)
    discharged_at = models.DateTimeField(null=True, blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bed_assignments_made",
    )
    discharged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bed_assignments_closed",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-assigned_at"]
        indexes = [
            models.Index(fields=["bed", "assigned_at"]),
            models.Index(fields=["patient", "assigned_at"]),
        ]

    @property
    def is_active(self):
        return self.discharged_at is None

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        refresh_bed_status(self.bed)

    def delete(self, *args, **kwargs):
        bed = self.bed
        super().delete(*args, **kwargs)
        refresh_bed_status(bed)

    def __str__(self):
        status = "active" if self.is_active else "closed"
        return f"{self.patient} -> {self.bed} ({status})"


class FacilityRolePermission(models.Model):
    """
    Facility-specific role permissions. Allows Super Admins to control what each role can do.
    If no permissions are configured for a role, all actions are allowed (backward compatible).
    """
    facility = models.ForeignKey(
        Facility,
        on_delete=models.CASCADE,
        related_name="role_permissions"
    )
    role = models.CharField(
        max_length=32,
        choices=[
            (UserRole.DOCTOR, "Doctor"),
            (UserRole.NURSE, "Nurse"),
            (UserRole.LAB, "Lab Scientist"),
            (UserRole.PHARMACY, "Pharmacist"),
            (UserRole.FRONTDESK, "Front Desk"),
            (UserRole.ADMIN, "Admin"),
        ]
    )
    
    # Pharmacy permissions
    can_manage_pharmacy_catalog = models.BooleanField(
        default=True,
        help_text="Create, edit, delete drugs in pharmacy catalog"
    )
    can_manage_pharmacy_stock = models.BooleanField(
        default=True,
        help_text="Adjust pharmacy stock levels"
    )
    can_dispense_prescriptions = models.BooleanField(
        default=True,
        help_text="Dispense medications to patients"
    )
    can_view_prescriptions = models.BooleanField(
        default=True,
        help_text="View prescription orders"
    )
    
    # Lab permissions
    can_manage_lab_catalog = models.BooleanField(
        default=True,
        help_text="Create, edit, delete lab tests in catalog"
    )
    can_process_lab_orders = models.BooleanField(
        default=True,
        help_text="Collect samples and enter results"
    )
    can_view_lab_orders = models.BooleanField(
        default=True,
        help_text="View lab orders and results"
    )
    
    # Encounter permissions
    can_create_encounters = models.BooleanField(
        default=True,
        help_text="Start new patient encounters"
    )
    can_view_all_encounters = models.BooleanField(
        default=True,
        help_text="View all facility encounters (not just assigned ones)"
    )
    can_edit_encounters = models.BooleanField(
        default=True,
        help_text="Edit encounter details and SOAP notes"
    )
    can_close_encounters = models.BooleanField(
        default=True,
        help_text="Close and lock encounters"
    )
    can_assign_providers = models.BooleanField(
        default=True,
        help_text="Assign doctors to encounters"
    )
    
    # Appointment permissions
    can_manage_appointments = models.BooleanField(
        default=True,
        help_text="Create, edit, cancel appointments"
    )
    can_view_all_appointments = models.BooleanField(
        default=True,
        help_text="View all facility appointments"
    )
    can_check_in_appointments = models.BooleanField(
        default=True,
        help_text="Check in patients for appointments"
    )
    
    # Billing permissions
    can_create_charges = models.BooleanField(
        default=True,
        help_text="Create billing charges"
    )
    can_view_billing = models.BooleanField(
        default=True,
        help_text="View patient billing information"
    )
    can_manage_payments = models.BooleanField(
        default=True,
        help_text="Record and manage payments"
    )
    
    # Patient management
    can_create_patients = models.BooleanField(
        default=True,
        help_text="Register new patients"
    )
    can_view_all_patients = models.BooleanField(
        default=True,
        help_text="View all facility patients"
    )
    can_edit_patient_records = models.BooleanField(
        default=True,
        help_text="Edit patient information"
    )
    
    # Vital signs
    can_record_vitals = models.BooleanField(
        default=True,
        help_text="Record patient vital signs"
    )
    can_view_vitals = models.BooleanField(
        default=True,
        help_text="View patient vitals"
    )
    
    # Ward management
    can_manage_wards = models.BooleanField(
        default=True,
        help_text="Create and manage wards and beds"
    )
    can_assign_beds = models.BooleanField(
        default=True,
        help_text="Assign patients to beds"
    )
    can_discharge_patients = models.BooleanField(
        default=True,
        help_text="Discharge patients from beds"
    )
    
    # HMO pricing (sensitive)
    can_manage_hmo_pricing = models.BooleanField(
        default=False,
        help_text="Set and manage HMO-specific pricing"
    )
    
    # Facility settings
    can_manage_facility_settings = models.BooleanField(
        default=False,
        help_text="Edit facility information and settings"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="permission_updates"
    )
    
    class Meta:
        unique_together = ('facility', 'role')
        ordering = ['facility', 'role']
        
    def __str__(self):
        return f"{self.facility.name} - {self.get_role_display()}"