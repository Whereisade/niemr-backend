from django.db import transaction
from rest_framework import serializers
from accounts.models import User
from django.utils import timezone
from accounts.enums import UserRole
from .models import Patient, PatientDocument, HMO, Allergy
from .enums import BloodGroup, Genotype, InsuranceStatus, AllergyType, AllergySeverity
from rest_framework import serializers as rf_serializers
from .models import SystemHMO, HMOTier, FacilityHMO, PatientFacilityHMOApproval


# ============================================================================
# LEGACY HMO SERIALIZER (Facility-scoped)
# ============================================================================

class HMOSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for HMO details in patient serializers.
    Shows full HMO information including contact details.
    """
    primary_address = serializers.SerializerMethodField()
    primary_contact = serializers.SerializerMethodField()
    
    class Meta:
        model = HMO
        fields = [
            "id",
            "name",
            "email",
            "nhis_number",
            "addresses",
            "contact_numbers",
            "primary_address",
            "primary_contact",
            "contact_person_name",
            "contact_person_phone",
            "contact_person_email",
        ]
    
    def get_primary_address(self, obj):
        """Get the first address from the list"""
        return obj.get_primary_address()
    
    def get_primary_contact(self, obj):
        """Get the first contact number from the list"""
        return obj.get_primary_contact()


# ============================================================================
# SYSTEM HMO SERIALIZERS (moved before PatientSerializer)
# ============================================================================

class HMOTierMinimalSerializer(serializers.ModelSerializer):
    """Minimal tier serializer for nested use in patient data."""
    
    class Meta:
        model = HMOTier
        fields = [
            'id',
            'name',
            'level',
            'description',
            'is_active'
        ]


class SystemHMOMinimalSerializer(serializers.ModelSerializer):
    """Minimal HMO serializer with contact info for patient views."""
    
    primary_address = serializers.SerializerMethodField()
    primary_contact = serializers.SerializerMethodField()
    
    class Meta:
        model = SystemHMO
        fields = [
            'id',
            'name',
            'nhis_number',
            'email',
            'addresses',
            'contact_numbers',
            'primary_address',
            'primary_contact',
            'contact_person_name',
            'contact_person_phone',
            'contact_person_email'
        ]
    
    def get_primary_address(self, obj):
        """Return first address or empty string"""
        if obj.addresses and len(obj.addresses) > 0:
            return obj.addresses[0]
        return ""
    
    def get_primary_contact(self, obj):
        """Return first contact number or empty string"""
        if obj.contact_numbers and len(obj.contact_numbers) > 0:
            return obj.contact_numbers[0]
        return ""


# ============================================================================
# PATIENT SERIALIZERS
# ============================================================================

class PatientSerializer(serializers.ModelSerializer):
    # Legacy HMO (facility-scoped)
    hmo = HMOSerializer(read_only=True)
    hmo_id = serializers.PrimaryKeyRelatedField(
        source="hmo", 
        queryset=HMO.objects.none(), 
        write_only=True, 
        required=False, 
        allow_null=True
    )
    
    # System HMO fields (UPDATED - use nested serializers)
    system_hmo = SystemHMOMinimalSerializer(read_only=True)
    hmo_tier = HMOTierMinimalSerializer(read_only=True)
    
    # Also keep computed fields for backward compatibility
    system_hmo_name = serializers.CharField(source='system_hmo.name', read_only=True)
    hmo_tier_name = serializers.CharField(source='hmo_tier.name', read_only=True)
    hmo_tier_level = serializers.IntegerField(source='hmo_tier.level', read_only=True)
    
    # HMO enrollment info
    hmo_enrollment_facility_name = serializers.CharField(
        source='hmo_enrollment_facility.name', 
        read_only=True
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Scope HMO choices to the requester's facility.
        req = self.context.get("request") if hasattr(self, "context") else None
        u = getattr(req, "user", None) if req else None
        facility_id = getattr(u, "facility_id", None)
        if facility_id:
            self.fields["hmo_id"].queryset = HMO.objects.filter(facility_id=facility_id, is_active=True)
        else:
            self.fields["hmo_id"].queryset = HMO.objects.none()

    class Meta:
        model = Patient
        fields = [
            "id", "user", "facility", "guardian_user",
            "first_name", "last_name", "middle_name", "dob", "gender",
            "email", "phone", "country", "state", "lga", "address",
            # Insurance (legacy)
            "insurance_status", "hmo", "hmo_id", "hmo_plan",
            "insurance_number", "insurance_expiry", "insurance_notes",
            # System HMO (new)
            "system_hmo", "system_hmo_name",
            "hmo_tier", "hmo_tier_name", "hmo_tier_level",
            "hmo_enrollment_facility", "hmo_enrollment_facility_name",
            "hmo_enrollment_provider", "hmo_enrolled_at",
            # Clinical
            "blood_group", "blood_group_other", "genotype", "genotype_other",
            "weight_kg", "height_cm", "bmi",
            "patient_status", "default_encounter_type",
            "emergency_contact_name", "emergency_contact_phone",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "bmi", "created_at", "updated_at", "user",
            "system_hmo_name", "hmo_tier_name", "hmo_tier_level",
            "hmo_enrollment_facility_name",
        ]


class PatientCreateByStaffSerializer(PatientSerializer):
    """
    For hospital staff / provider creating a patient into a facility.
    - facility comes from requester.user.facility if not provided
    """
    def create(self, validated):
        if not validated.get("facility") and self.context["request"].user.facility:
            validated["facility"] = self.context["request"].user.facility
        return super().create(validated)


class SelfRegisterSerializer(serializers.Serializer):
    """
    Self-registration: creates User(PATIENT) + Patient.
    """
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=120)
    last_name = serializers.CharField(max_length=120)
    dob = serializers.DateField()
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    country = serializers.CharField(max_length=120, required=False, allow_blank=True)
    state = serializers.CharField(max_length=120, required=False, allow_blank=True)
    lga = serializers.CharField(max_length=120, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)

    # optional clinical bits
    blood_group = serializers.ChoiceField(choices=BloodGroup.choices, required=False, allow_blank=True)
    blood_group_other = serializers.CharField(max_length=3, required=False, allow_blank=True)
    genotype = serializers.ChoiceField(choices=Genotype.choices, required=False, allow_blank=True)
    genotype_other = serializers.CharField(max_length=2, required=False, allow_blank=True)
    weight_kg = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    height_cm = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)

    @transaction.atomic
    def create(self, validated):
        email = validated["email"].strip().lower()
        password = validated["password"]
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError({"email": "Email is already registered."})

        user = User.objects.create_user(
            email,
            password=password,
            first_name=validated["first_name"],
            last_name=validated["last_name"],
            role=UserRole.PATIENT,
        )

        p_fields = {k: v for k, v in validated.items() if k not in ("email", "password")}
        patient = Patient.objects.create(user=user, **p_fields)
        return patient


# ============================================================================
# PATIENT DOCUMENT SERIALIZERS
# ============================================================================

class PatientDocumentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = PatientDocument
        fields = [
            "id",
            "patient",
            "title",
            "document_type",
            "file",
            "notes",
            "uploaded_by",
            "uploaded_by_name",
            "uploaded_by_role",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "patient",  
            "uploaded_by",
            "uploaded_by_role",
            "uploaded_by_name",
            "created_at",
        ]

    def get_uploaded_by_name(self, obj):
        user = obj.uploaded_by
        if not user:
            return None
        if hasattr(user, "get_full_name"):
            return user.get_full_name() or user.email
        return str(user)


# ============================================================================
# DEPENDENT SERIALIZERS
# ============================================================================

class DependentCreateSerializer(serializers.ModelSerializer):
    """
    Serializer used when creating a dependent under a parent patient.
    """
    relationship = serializers.CharField(
        max_length=32,
        required=False,
        allow_blank=True,
        help_text="Relationship of the dependent to the guardian (e.g. Son, Daughter)",
    )

    class Meta:
        model = Patient
        fields = [
            "first_name",
            "last_name",
            "dob",
            "gender",
            "relationship",
            "phone",
        ]

    def validate(self, attrs):
        if self.initial_data.get("user") or self.initial_data.get("user_id"):
            raise rf_serializers.ValidationError(
                "Dependents cannot be created with a linked user."
            )
        return attrs

    def create(self, validated_data):
        relationship = validated_data.pop("relationship", "").strip()
        if relationship:
            validated_data["relationship_to_guardian"] = relationship
        return Patient.objects.create(**validated_data)


class DependentSerializer(serializers.ModelSerializer):
    """
    Read serializer for dependent records.
    """
    relationship = serializers.CharField(
        source="relationship_to_guardian",
        read_only=True,
    )

    class Meta:
        model = Patient
        fields = [
            "id",
            "first_name",
            "last_name",
            "dob",
            "gender",
            "relationship",
            "phone",
        ]


class DependentUpdateSerializer(serializers.ModelSerializer):
    """
    Update serializer so clients can PATCH relationship as well.
    """
    relationship = serializers.CharField(
        max_length=32,
        required=False,
        allow_blank=True,
        help_text="Relationship of the dependent to the guardian (e.g. Son, Daughter)",
    )

    class Meta:
        model = Patient
        fields = [
            "first_name",
            "last_name",
            "dob",
            "gender",
            "relationship",
            "phone",
        ]

    def update(self, instance, validated_data):
        relationship = validated_data.pop("relationship", None)
        if relationship is not None:
            instance.relationship_to_guardian = (relationship or "").strip()
        return super().update(instance, validated_data)


# ============================================================================
# ALLERGY SERIALIZERS
# ============================================================================

class AllergySerializer(serializers.ModelSerializer):
    """
    Read/write serializer for patient allergies.
    """
    recorded_by_name = serializers.SerializerMethodField(read_only=True)
    patient_name = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = Allergy
        fields = [
            "id",
            "patient",
            "patient_name",
            "allergen",
            "allergy_type",
            "severity",
            "reaction",
            "onset_date",
            "notes",
            "is_active",
            "recorded_by",
            "recorded_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "patient",
            "patient_name",
            "recorded_by",
            "recorded_by_name",
            "created_at",
            "updated_at",
        ]
    
    def get_recorded_by_name(self, obj):
        user = obj.recorded_by
        if not user:
            return None
        if hasattr(user, "get_full_name"):
            return user.get_full_name() or user.email
        return str(user)
    
    def get_patient_name(self, obj):
        if obj.patient:
            return f"{obj.patient.first_name} {obj.patient.last_name}"
        return None


class AllergyCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating allergies.
    """
    
    class Meta:
        model = Allergy
        fields = [
            "allergen",
            "allergy_type",
            "severity",
            "reaction",
            "onset_date",
            "notes",
        ]
    
    def validate_allergen(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Allergen is required.")
        return value.strip()


class AllergyUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for updating allergies.
    """
    
    class Meta:
        model = Allergy
        fields = [
            "allergen",
            "allergy_type",
            "severity",
            "reaction",
            "onset_date",
            "notes",
            "is_active",
        ]


# ============================================================================
# SYSTEM HMO SERIALIZERS
# ============================================================================

class HMOTierSerializer(serializers.ModelSerializer):
    """Serializer for HMO Tiers."""
    
    class Meta:
        model = HMOTier
        fields = [
            'id',
            'system_hmo',
            'name',
            'level',
            'description',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class HMOTierMinimalSerializer(serializers.ModelSerializer):
    """Minimal tier serializer for nested use in patient data."""
    
    class Meta:
        model = HMOTier
        fields = [
            'id',
            'name',
            'level',
            'description',
            'is_active'
        ]


class SystemHMOSerializer(serializers.ModelSerializer):
    """Serializer for SystemHMO with tier summary."""
    
    tiers = HMOTierMinimalSerializer(many=True, read_only=True)
    tier_count = serializers.SerializerMethodField()
    facility_count = serializers.SerializerMethodField()
    
    class Meta:
        model = SystemHMO
        fields = [
            'id',
            'name',
            'nhis_number',
            'email',
            'addresses',
            'contact_numbers',
            'contact_person_name',
            'contact_person_phone',
            'contact_person_email',
            'website',
            'description',
            'is_active',
            'tiers',
            'tier_count',
            'facility_count',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'tiers', 'tier_count', 'facility_count']
    
    def get_tier_count(self, obj):
        return obj.tiers.filter(is_active=True).count()
    
    def get_facility_count(self, obj):
        return obj.facility_links.filter(is_active=True).count()


class SystemHMODetailSerializer(SystemHMOSerializer):
    """Detailed SystemHMO serializer with full tier information."""
    
    tiers = HMOTierSerializer(many=True, read_only=True)


class SystemHMOMinimalSerializer(serializers.ModelSerializer):
    """Minimal HMO serializer with contact info for patient views."""
    
    primary_address = serializers.SerializerMethodField()
    primary_contact = serializers.SerializerMethodField()
    
    class Meta:
        model = SystemHMO
        fields = [
            'id',
            'name',
            'nhis_number',
            'email',
            'addresses',
            'contact_numbers',
            'primary_address',
            'primary_contact',
            'contact_person_name',
            'contact_person_phone',
            'contact_person_email'
        ]
    
    def get_primary_address(self, obj):
        """Return first address or empty string"""
        if obj.addresses and len(obj.addresses) > 0:
            return obj.addresses[0]
        return ""
    
    def get_primary_contact(self, obj):
        """Return first contact number or empty string"""
        if obj.contact_numbers and len(obj.contact_numbers) > 0:
            return obj.contact_numbers[0]
        return ""


class SystemHMOListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for listing System HMOs.
    Used in dropdowns and selection lists.
    """
    tiers = HMOTierMinimalSerializer(many=True, read_only=True)
    
    class Meta:
        model = SystemHMO
        fields = [
            'id',
            'name',
            'nhis_number',
            'is_active',
            'tiers',
        ]


class SystemHMOCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating new System HMOs (admin only).
    Tiers are auto-created on save.
    """
    
    class Meta:
        model = SystemHMO
        fields = [
            'name',
            'nhis_number',
            'email',
            'addresses',
            'contact_numbers',
            'contact_person_name',
            'contact_person_phone',
            'contact_person_email',
            'website',
            'description',
            'is_active',
        ]
    
    def validate_name(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("HMO name is required.")
        
        # Check for duplicates
        if SystemHMO.objects.filter(name__iexact=value.strip()).exists():
            raise serializers.ValidationError("An HMO with this name already exists.")
        
        return value.strip()
    
    def validate_addresses(self, value):
        """Ensure addresses is a list and filter empty values."""
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("Addresses must be a list.")
        return [addr.strip() for addr in value if addr and addr.strip()]
    
    def validate_contact_numbers(self, value):
        """Ensure contact_numbers is a list and filter empty values."""
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("Contact numbers must be a list.")
        return [num.strip() for num in value if num and num.strip()]


# ============================================================================
# FACILITY HMO SERIALIZERS
# ============================================================================

class FacilityHMOSerializer(serializers.ModelSerializer):
    """Serializer for FacilityHMO relationships."""
    
    system_hmo = SystemHMOMinimalSerializer(read_only=True)
    facility_name = serializers.CharField(source='facility.name', read_only=True, allow_null=True)
    owner_name = serializers.SerializerMethodField()
    relationship_updated_by_name = serializers.SerializerMethodField()
    
    # Add computed fields for primary contact info
    primary_address = serializers.SerializerMethodField()
    primary_contact = serializers.SerializerMethodField()
    
    class Meta:
        model = FacilityHMO
        fields = [
            'id',
            'facility',
            'facility_name',
            'owner',
            'owner_name',
            'system_hmo',
            
            # Contact Information
            'email',
            'addresses',
            'contact_numbers',
            'primary_address',
            'primary_contact',
            'contact_person_name',
            'contact_person_phone',
            'contact_person_email',
            'nhis_number',
            
            # Relationship tracking
            'relationship_status',
            'relationship_notes',
            'relationship_updated_at',
            'relationship_updated_by',
            'relationship_updated_by_name',
            
            # Contract details
            'contract_start_date',
            'contract_end_date',
            'contract_reference',
            
            # Status
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at',
            'relationship_updated_at', 'relationship_updated_by',
            'primary_address', 'primary_contact'
        ]
    
    def get_primary_address(self, obj):
        """Get the first address from the list"""
        if obj.addresses and len(obj.addresses) > 0:
            return obj.addresses[0]
        return ''
    
    def get_primary_contact(self, obj):
        """Get the first contact number from the list"""
        if obj.contact_numbers and len(obj.contact_numbers) > 0:
            return obj.contact_numbers[0]
        return ''
    
    def get_owner_name(self, obj):
        if obj.owner:
            return f"{obj.owner.first_name} {obj.owner.last_name}".strip() or obj.owner.email
        return None
    
    def get_relationship_updated_by_name(self, obj):
        if obj.relationship_updated_by:
            return f"{obj.relationship_updated_by.first_name} {obj.relationship_updated_by.last_name}".strip()
        return None


class FacilityHMOCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating FacilityHMO relationships."""
    
    system_hmo_id = serializers.PrimaryKeyRelatedField(
        queryset=SystemHMO.objects.filter(is_active=True),
        source='system_hmo'
    )
    
    class Meta:
        model = FacilityHMO
        fields = [
            'system_hmo_id',
            'relationship_status',
            'relationship_notes',
            'contract_start_date',
            'contract_end_date',
            'contract_reference',
        ]


class FacilityHMOUpdateRelationshipSerializer(serializers.Serializer):
    """
    Serializer for updating relationship status with an HMO.
    """
    relationship_status = serializers.ChoiceField(
        choices=FacilityHMO.RelationshipStatus.choices,
        required=True
    )
    relationship_notes = serializers.CharField(required=False, allow_blank=True)
    
    def update(self, instance, validated_data):
        request = self.context.get('request')
        
        instance.relationship_status = validated_data['relationship_status']
        instance.relationship_notes = validated_data.get('relationship_notes', instance.relationship_notes)
        instance.relationship_updated_at = timezone.now()
        instance.relationship_updated_by = request.user
        
        instance.save(update_fields=[
            'relationship_status',
            'relationship_notes',
            'relationship_updated_at',
            'relationship_updated_by',
            'updated_at',
        ])
        
        return instance


# ============================================================================
# PATIENT HMO ENROLLMENT SERIALIZERS
# ============================================================================

class PatientAttachHMOSerializer(serializers.Serializer):
    """Serializer for attaching HMO to a patient."""
    
    system_hmo_id = serializers.IntegerField(required=True)
    tier_id = serializers.IntegerField(required=True)
    insurance_number = serializers.CharField(max_length=120, required=False, allow_blank=True)
    insurance_expiry = serializers.DateField(required=False, allow_null=True)
    insurance_notes = serializers.CharField(required=False, allow_blank=True)
    
    def validate_system_hmo_id(self, value):
        if not SystemHMO.objects.filter(id=value, is_active=True).exists():
            raise serializers.ValidationError('HMO not found or is inactive')
        return value
    
    def validate(self, attrs):
        system_hmo_id = attrs.get('system_hmo_id')
        tier_id = attrs.get('tier_id')
        
        if not HMOTier.objects.filter(
            id=tier_id,
            system_hmo_id=system_hmo_id,
            is_active=True
        ).exists():
            raise serializers.ValidationError({
                'tier_id': 'Tier not found or does not belong to this HMO'
            })
        
        return attrs


class PatientTransferHMOApprovalSerializer(serializers.Serializer):
    """
    Serializer for approving/rejecting a patient's HMO transfer.
    """
    action = serializers.ChoiceField(choices=['approve', 'reject'], required=True)
    notes = serializers.CharField(required=False, allow_blank=True)


# ============================================================================
# PATIENT HMO APPROVAL SERIALIZERS
# ============================================================================

class PatientFacilityHMOApprovalSerializer(serializers.ModelSerializer):
    """Serializer for HMO transfer approval requests."""
    
    patient_name = serializers.SerializerMethodField()
    system_hmo = SystemHMOMinimalSerializer(read_only=True)
    tier = HMOTierMinimalSerializer(read_only=True)
    facility_name = serializers.CharField(source='facility.name', read_only=True)
    owner_name = serializers.SerializerMethodField()
    original_facility_name = serializers.CharField(
        source='original_facility.name',
        read_only=True
    )
    original_provider_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = PatientFacilityHMOApproval
        fields = [
            'id',
            'patient',
            'patient_name',
            'facility',
            'facility_name',
            'owner',
            'owner_name',
            'system_hmo',
            'tier',
            'insurance_number',
            'insurance_expiry',
            'status',
            'status_display',
            'requested_at',
            'decided_at',
            'decided_by',
            'decided_by_name',
            'request_notes',
            'decision_notes',
            'original_facility',
            'original_facility_name',
            'original_provider',
            'original_provider_name',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'patient', 'facility', 'owner', 'system_hmo', 'tier',
            'status', 'requested_at', 'decided_at', 'decided_by',
            'original_facility', 'original_provider',
            'created_at', 'updated_at'
        ]
    
    def get_patient_name(self, obj):
        if obj.patient:
            return f"{obj.patient.first_name} {obj.patient.last_name}"
        return None
    
    def get_owner_name(self, obj):
        if obj.owner:
            return f"{obj.owner.first_name} {obj.owner.last_name}".strip() or obj.owner.email
        return None
    
    def get_original_provider_name(self, obj):
        if obj.original_provider:
            return f"{obj.original_provider.first_name} {obj.original_provider.last_name}".strip() or obj.original_provider.email
        return None
    
    def get_decided_by_name(self, obj):
        if obj.decided_by:
            return f"{obj.decided_by.first_name} {obj.decided_by.last_name}".strip() or obj.decided_by.email
        return None


class PatientFacilityHMOApprovalCreateSerializer(serializers.Serializer):
    """
    Create a new HMO approval request when patient transfers.
    """
    patient_id = serializers.IntegerField(required=True)
    notes = serializers.CharField(required=False, allow_blank=True)


# ============================================================================
# HMO PRICE SERIALIZERS (for billing integration)
# ============================================================================

class HMOTierPriceSerializer(serializers.Serializer):
    """
    Serializer for tier-specific pricing.
    Used when setting prices per HMO tier.
    """
    tier_id = serializers.IntegerField(required=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    
    def validate_tier_id(self, value):
        try:
            tier = HMOTier.objects.get(id=value, is_active=True)
            self.context['tier'] = tier
            return value
        except HMOTier.DoesNotExist:
            raise serializers.ValidationError("Invalid tier.")
    
    def validate_amount(self, value):
        if value < 0:
            raise serializers.ValidationError("Amount cannot be negative.")
        return value


class PatientHMOInfoSerializer(serializers.Serializer):
    """Serializer for patient HMO information display."""
    
    has_hmo = serializers.BooleanField()
    system_hmo = SystemHMOMinimalSerializer(allow_null=True)
    tier = HMOTierMinimalSerializer(allow_null=True)
    insurance_number = serializers.CharField(allow_null=True)
    insurance_expiry = serializers.DateField(allow_null=True)
    enrolled_at = serializers.DateTimeField(allow_null=True)
    enrollment_source = serializers.SerializerMethodField()
    
    def get_enrollment_source(self, obj):
        if obj.get('enrollment_facility'):
            return {
                'type': 'FACILITY',
                'id': obj['enrollment_facility'].id,
                'name': obj['enrollment_facility'].name,
            }
        elif obj.get('enrollment_provider'):
            return {
                'type': 'INDEPENDENT',
                'id': obj['enrollment_provider'].id,
                'name': f"{obj['enrollment_provider'].first_name} {obj['enrollment_provider'].last_name}".strip(),
            }
        return None


# ===========================================
# PATIENT SERIALIZER MIXIN
# ===========================================

class PatientHMOMixin(serializers.Serializer):
    """
    Mixin to add HMO fields to patient serializers.
    
    Use this in your existing PatientSerializer:
    
    class PatientSerializer(PatientHMOMixin, serializers.ModelSerializer):
        ...
    """
    
    # Read-only computed fields
    system_hmo_display = SystemHMOMinimalSerializer(source='system_hmo', read_only=True)
    hmo_tier_display = HMOTierMinimalSerializer(source='hmo_tier', read_only=True)
    hmo_enrollment_info = serializers.SerializerMethodField()
    has_hmo = serializers.SerializerMethodField()
    
    def get_hmo_enrollment_info(self, obj):
        if not obj.system_hmo:
            return None
        
        info = {
            'enrolled_at': obj.hmo_enrolled_at,
        }
        
        if obj.hmo_enrollment_facility:
            info['source_type'] = 'FACILITY'
            info['source_id'] = obj.hmo_enrollment_facility_id
            info['source_name'] = obj.hmo_enrollment_facility.name
        elif obj.hmo_enrollment_provider:
            info['source_type'] = 'INDEPENDENT'
            info['source_id'] = obj.hmo_enrollment_provider_id
            info['source_name'] = f"{obj.hmo_enrollment_provider.first_name} {obj.hmo_enrollment_provider.last_name}".strip()
        
        return info
    
    def get_has_hmo(self, obj):
        return obj.system_hmo_id is not None