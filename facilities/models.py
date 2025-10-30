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

    CONTROLLED_BY_CHOICES = [
        ('federal', 'Federal'),
        ('state', 'State'),
        ('church', 'Church'),
        ('ngo', 'NGO'),
        ('private', 'Private'),
    ]

    COUNTRY_CHOICES = [
        ('nigeria', 'Nigeria'),
        ('ghana', 'Ghana'),
        ('kenya', 'Kenya'),
        ('south_africa', 'South Africa'),
    ]

    # Nigerian states
    NIGERIA_STATES = [
        ('abia', 'Abia'), ('adamawa', 'Adamawa'), ('akwa_ibom', 'Akwa Ibom'),
        ('anambra', 'Anambra'), ('bauchi', 'Bauchi'), ('bayelsa', 'Bayelsa'),
        ('benue', 'Benue'), ('borno', 'Borno'), ('cross_river', 'Cross River'),
        ('delta', 'Delta'), ('ebonyi', 'Ebonyi'), ('edo', 'Edo'),
        ('ekiti', 'Ekiti'), ('enugu', 'Enugu'), ('gombe', 'Gombe'),
        ('imo', 'Imo'), ('jigawa', 'Jigawa'), ('kaduna', 'Kaduna'),
        ('kano', 'Kano'), ('katsina', 'Katsina'), ('kebbi', 'Kebbi'),
        ('kogi', 'Kogi'), ('kwara', 'Kwara'), ('lagos', 'Lagos'),
        ('nasarawa', 'Nasarawa'), ('niger', 'Niger'), ('ogun', 'Ogun'),
        ('ondo', 'Ondo'), ('osun', 'Osun'), ('oyo', 'Oyo'),
        ('plateau', 'Plateau'), ('rivers', 'Rivers'), ('sokoto', 'Sokoto'),
        ('taraba', 'Taraba'), ('yobe', 'Yobe'), ('zamfara', 'Zamfara'),
        ('fct', 'FCT - Abuja'),
    ]

    # Ghana regions
    GHANA_REGIONS = [
        ('ashanti', 'Ashanti'), ('brong_ahafo', 'Brong-Ahafo'), ('central', 'Central'),
        ('eastern', 'Eastern'), ('greater_accra', 'Greater Accra'),
        ('northern', 'Northern'), ('savannah', 'Savannah'), ('upper_east', 'Upper East'),
        ('upper_west', 'Upper West'), ('volta', 'Volta'),
        ('western', 'Western'), ('western_north', 'Western North'),
    ]

    # Kenya counties
    KENYA_COUNTIES = [
        ('nairobi', 'Nairobi'), ('mombasa', 'Mombasa'), ('kisumu', 'Kisumu'),
        ('nakuru', 'Nakuru'), ('eldoret', 'Eldoret'), ('machakos', 'Machakos'),
        ('nyeri', 'Nyeri'), ('kisii', 'Kisii'), ('muranga', "Murang'a"),
        ('garissa', 'Garissa'), ('lamu', 'Lamu'), ('marsabit', 'Marsabit'),
        ('kilifi', 'Kilifi'), ('bomet', 'Bomet'), ('turkana', 'Turkana'),
    ]

    # South Africa provinces
    SOUTH_AFRICA_PROVINCES = [
        ('eastern_cape', 'Eastern Cape'), ('free_state', 'Free State'),
        ('gauteng', 'Gauteng'), ('kwazulu_natal', 'KwaZulu-Natal'),
        ('limpopo', 'Limpopo'), ('mpumalanga', 'Mpumalanga'),
        ('northern_cape', 'Northern Cape'), ('north_west', 'North West'),
        ('western_cape', 'Western Cape'),
    ]

    # Combine all options into one big STATE_CHOICES list
    STATE_CHOICES = (
        [('---nigeria---', '--- Nigeria ---')] + NIGERIA_STATES +
        [('---ghana---', '--- Ghana ---')] + GHANA_REGIONS +
        [('---kenya---', '--- Kenya ---')] + KENYA_COUNTIES +
        [('---south_africa---', '--- South Africa ---')] + SOUTH_AFRICA_PROVINCES
    )

    name = models.CharField(max_length=255)
    controlled_by = models.CharField(
        max_length=20,
        choices=CONTROLLED_BY_CHOICES,
        default='private'
    )
    country = models.CharField(
        max_length=50,
        choices=COUNTRY_CHOICES,
        default='nigeria'
    )
    state = models.CharField(
        max_length=50,
        choices=STATE_CHOICES,
        blank=True,
        null=True
    )
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
