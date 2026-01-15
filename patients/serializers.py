from django.db import transaction
from rest_framework import serializers
from accounts.models import User
from django.utils import timezone
from accounts.enums import UserRole
from .models import Patient, PatientDocument, HMO, Allergy
from .enums import BloodGroup, Genotype, InsuranceStatus, AllergyType, AllergySeverity
from rest_framework import serializers as rf_serializers
from .models import SystemHMO, HMOTier, FacilityHMO, PatientFacilityHMOApproval




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


class PatientSerializer(serializers.ModelSerializer):
    hmo = HMOSerializer(read_only=True)
    hmo_id = serializers.PrimaryKeyRelatedField(source="hmo", queryset=HMO.objects.none(), write_only=True, required=False, allow_null=True)

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
            "id","user","facility","guardian_user",
            "first_name","last_name","middle_name","dob","gender",
            "email","phone","country","state","lga","address",
            "insurance_status","hmo","hmo_id","hmo_plan",
            "insurance_number","insurance_expiry","insurance_notes",
            "blood_group","blood_group_other","genotype","genotype_other",
            "weight_kg","height_cm","bmi",
            "patient_status","default_encounter_type",
            "emergency_contact_name","emergency_contact_phone",
            "created_at","updated_at",
        ]
        read_only_fields = ["bmi","created_at","updated_at","user"]


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
        # 1) Create User with PATIENT role
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

        # 2) Create Patient linked to user
        p_fields = {k: v for k, v in validated.items() if k not in ("email","password")}
        patient = Patient.objects.create(user=user, **p_fields)
        return patient


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


# --- Dependent serializers ---

class DependentCreateSerializer(serializers.ModelSerializer):
    """
    Serializer used when creating a dependent under a parent patient.

    Incoming payload (from frontend) can use:
      - first_name
      - last_name
      - dob
      - gender
      - relationship  (e.g. "Son", "Daughter")
      - phone

    Internally we:
      - store `relationship` into `relationship_to_guardian`
      - set `parent_patient` from the view (perform_create / nested action)
      - optionally set `guardian_user` from request.user
    """

    # Expose a simple "relationship" field to the client
    relationship = serializers.CharField(
        max_length=32,
        required=False,
        allow_blank=True,
        help_text="Relationship of the dependent to the guardian (e.g. Son, Daughter)",
    )

    class Meta:
        model = Patient
        # NOTE: No `relationship_to_guardian` here; we map manually.
        fields = [
            "first_name",
            "last_name",
            "dob",
            "gender",
            "relationship",
            "phone",
        ]

    def validate(self, attrs):
        """
        Keep the existing guard: block attempts to attach a User directly.
        """
        if self.initial_data.get("user") or self.initial_data.get("user_id"):
            raise rf_serializers.ValidationError(
                "Dependents cannot be created with a linked user."
            )
        return attrs

    def create(self, validated_data):
        """
        Map `relationship` → `relationship_to_guardian`,
        and let the view inject `parent_patient`, `guardian_user`, `facility`
        via the `.save()` call.
        """
        relationship = validated_data.pop("relationship", "").strip()
        if relationship:
            validated_data["relationship_to_guardian"] = relationship

        # parent_patient / guardian_user / facility come from serializer.save(...)
        return Patient.objects.create(**validated_data)


class DependentSerializer(serializers.ModelSerializer):
    """
    Read serializer for dependent records.
    Exposes `relationship` as a friendly alias of `relationship_to_guardian`.
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


# --- Allergy serializers ---

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
    Patient is set by the view based on context.
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



class HMOTierSerializer(serializers.ModelSerializer):
    """
    Serializer for HMO tiers.
    Read-only for most users, writable only by system admins.
    """
    
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
    """Minimal tier serializer for nested use in patient/HMO serializers."""
    
    class Meta:
        model = HMOTier
        fields = ['id', 'name', 'level']


class SystemHMOSerializer(serializers.ModelSerializer):
    """
    Full serializer for System HMOs.
    Includes nested tiers.
    """
    tiers = HMOTierSerializer(many=True, read_only=True)
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
            'contact_person_email',
            'website',
            'description',
            'is_active',
            'tiers',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'tiers', 'created_at', 'updated_at']
    
    def get_primary_address(self, obj):
        return obj.get_primary_address()
    
    def get_primary_contact(self, obj):
        return obj.get_primary_contact()


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
    """
    Full serializer for Facility-HMO relationships.
    Shows the system HMO details and relationship status.
    """
    system_hmo_details = SystemHMOListSerializer(source='system_hmo', read_only=True)
    system_hmo_name = serializers.CharField(source='system_hmo.name', read_only=True)
    scope_name = serializers.SerializerMethodField()
    relationship_updated_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = FacilityHMO
        fields = [
            'id',
            'facility',
            'owner',
            'system_hmo',
            'system_hmo_name',
            'system_hmo_details',
            'scope_name',
            'relationship_status',
            'relationship_notes',
            'relationship_updated_at',
            'relationship_updated_by',
            'relationship_updated_by_name',
            'contract_start_date',
            'contract_end_date',
            'contract_reference',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'facility',
            'owner',
            'system_hmo_name',
            'system_hmo_details',
            'scope_name',
            'relationship_updated_at',
            'relationship_updated_by',
            'relationship_updated_by_name',
            'created_at',
            'updated_at',
        ]
    
    def get_scope_name(self, obj):
        return obj.get_scope_name()
    
    def get_relationship_updated_by_name(self, obj):
        if not obj.relationship_updated_by:
            return None
        user = obj.relationship_updated_by
        name = f"{user.first_name} {user.last_name}".strip()
        return name or user.email


class FacilityHMOCreateSerializer(serializers.Serializer):
    """
    Serializer for enabling a System HMO for a facility/provider.
    """
    system_hmo_id = serializers.IntegerField(required=True)
    relationship_notes = serializers.CharField(required=False, allow_blank=True)
    contract_start_date = serializers.DateField(required=False, allow_null=True)
    contract_end_date = serializers.DateField(required=False, allow_null=True)
    contract_reference = serializers.CharField(required=False, allow_blank=True, max_length=120)
    
    def validate_system_hmo_id(self, value):
        try:
            system_hmo = SystemHMO.objects.get(id=value, is_active=True)
            self.context['system_hmo'] = system_hmo
            return value
        except SystemHMO.DoesNotExist:
            raise serializers.ValidationError("System HMO not found or inactive.")
    
    def validate(self, attrs):
        request = self.context.get('request')
        user = request.user
        
        facility = getattr(user, 'facility', None)
        system_hmo = self.context.get('system_hmo')
        
        # Check if this HMO is already enabled
        if facility:
            if FacilityHMO.objects.filter(facility=facility, system_hmo=system_hmo).exists():
                raise serializers.ValidationError({
                    "system_hmo_id": "This HMO is already enabled for your facility."
                })
        else:
            # Independent provider
            if FacilityHMO.objects.filter(owner=user, system_hmo=system_hmo).exists():
                raise serializers.ValidationError({
                    "system_hmo_id": "This HMO is already enabled for your practice."
                })
        
        # Validate contract dates
        if attrs.get('contract_start_date') and attrs.get('contract_end_date'):
            if attrs['contract_start_date'] > attrs['contract_end_date']:
                raise serializers.ValidationError({
                    "contract_end_date": "End date must be after start date."
                })
        
        return attrs
    
    @transaction.atomic
    def create(self, validated_data):
        request = self.context.get('request')
        user = request.user
        system_hmo = self.context.get('system_hmo')
        
        facility = getattr(user, 'facility', None)
        
        facility_hmo = FacilityHMO.objects.create(
            facility=facility if facility else None,
            owner=user if not facility else None,
            system_hmo=system_hmo,
            relationship_status=FacilityHMO.RelationshipStatus.GOOD,
            relationship_notes=validated_data.get('relationship_notes', ''),
            contract_start_date=validated_data.get('contract_start_date'),
            contract_end_date=validated_data.get('contract_end_date'),
            contract_reference=validated_data.get('contract_reference', ''),
            is_active=True,
        )
        
        return facility_hmo


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
    """
    Serializer for attaching a patient to an HMO.
    
    Used when:
    1. Initial HMO enrollment (patient has no HMO)
    2. Changing HMO
    """
    system_hmo_id = serializers.IntegerField(required=True)
    tier_id = serializers.IntegerField(required=True)
    insurance_number = serializers.CharField(required=False, allow_blank=True, max_length=120)
    insurance_expiry = serializers.DateField(required=False, allow_null=True)
    insurance_notes = serializers.CharField(required=False, allow_blank=True)
    
    def validate_system_hmo_id(self, value):
        try:
            system_hmo = SystemHMO.objects.get(id=value, is_active=True)
            self.context['system_hmo'] = system_hmo
            return value
        except SystemHMO.DoesNotExist:
            raise serializers.ValidationError("HMO not found or inactive.")
    
    def validate_tier_id(self, value):
        system_hmo = self.context.get('system_hmo')
        if not system_hmo:
            # Will be validated after system_hmo_id
            self.context['_tier_id'] = value
            return value
        
        try:
            tier = HMOTier.objects.get(id=value, system_hmo=system_hmo, is_active=True)
            self.context['tier'] = tier
            return value
        except HMOTier.DoesNotExist:
            raise serializers.ValidationError("Invalid tier for this HMO.")
    
    def validate(self, attrs):
        request = self.context.get('request')
        user = request.user
        
        system_hmo = self.context.get('system_hmo')
        
        # Validate tier against system_hmo if we have deferred validation
        if self.context.get('_tier_id') and system_hmo:
            try:
                tier = HMOTier.objects.get(
                    id=self.context['_tier_id'],
                    system_hmo=system_hmo,
                    is_active=True
                )
                self.context['tier'] = tier
            except HMOTier.DoesNotExist:
                raise serializers.ValidationError({
                    "tier_id": "Invalid tier for this HMO."
                })
        
        # Check if facility/provider has this HMO enabled
        facility = getattr(user, 'facility', None)
        
        if facility:
            if not FacilityHMO.objects.filter(
                facility=facility,
                system_hmo=system_hmo,
                is_active=True
            ).exists():
                raise serializers.ValidationError({
                    "system_hmo_id": "Your facility has not enabled this HMO. Please enable it first."
                })
        else:
            # Independent provider
            if not FacilityHMO.objects.filter(
                owner=user,
                system_hmo=system_hmo,
                is_active=True
            ).exists():
                raise serializers.ValidationError({
                    "system_hmo_id": "You have not enabled this HMO. Please enable it first."
                })
        
        return attrs


class PatientTransferHMOApprovalSerializer(serializers.Serializer):
    """
    Serializer for approving/rejecting a patient's HMO transfer.
    
    Used when a patient with existing HMO registers at a new facility.
    """
    action = serializers.ChoiceField(choices=['approve', 'reject'], required=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    
    def validate(self, attrs):
        request = self.context.get('request')
        approval = self.context.get('approval')
        
        if not approval:
            raise serializers.ValidationError("Approval record not found.")
        
        if approval.status != PatientFacilityHMOApproval.Status.PENDING:
            raise serializers.ValidationError("This request has already been processed.")
        
        return attrs


# ============================================================================
# PATIENT HMO APPROVAL SERIALIZERS
# ============================================================================

class PatientFacilityHMOApprovalSerializer(serializers.ModelSerializer):
    """
    Serializer for patient HMO approval requests.
    """
    patient_name = serializers.SerializerMethodField()
    system_hmo_name = serializers.CharField(source='system_hmo.name', read_only=True)
    tier_name = serializers.CharField(source='tier.name', read_only=True)
    tier_level = serializers.IntegerField(source='tier.level', read_only=True)
    original_facility_name = serializers.SerializerMethodField()
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
            'owner',
            'system_hmo',
            'system_hmo_name',
            'tier',
            'tier_name',
            'tier_level',
            'insurance_number',
            'insurance_expiry',
            'original_facility',
            'original_facility_name',
            'original_provider',
            'original_provider_name',
            'status',
            'status_display',
            'requested_at',
            'decided_at',
            'decided_by',
            'decided_by_name',
            'request_notes',
            'decision_notes',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'patient',
            'patient_name',
            'facility',
            'owner',
            'system_hmo_name',
            'tier_name',
            'tier_level',
            'original_facility_name',
            'original_provider_name',
            'status_display',
            'requested_at',
            'decided_at',
            'decided_by',
            'decided_by_name',
            'created_at',
            'updated_at',
        ]
    
    def get_patient_name(self, obj):
        if obj.patient:
            return f"{obj.patient.first_name} {obj.patient.last_name}".strip()
        return None
    
    def get_original_facility_name(self, obj):
        if obj.original_facility:
            return obj.original_facility.name
        return None
    
    def get_original_provider_name(self, obj):
        if obj.original_provider:
            name = f"{obj.original_provider.first_name} {obj.original_provider.last_name}".strip()
            return name or obj.original_provider.email
        return None
    
    def get_decided_by_name(self, obj):
        if obj.decided_by:
            name = f"{obj.decided_by.first_name} {obj.decided_by.last_name}".strip()
            return name or obj.decided_by.email
        return None


class PatientFacilityHMOApprovalCreateSerializer(serializers.Serializer):
    """
    Create a new HMO approval request when patient transfers.
    """
    patient_id = serializers.IntegerField(required=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    
    def validate_patient_id(self, value):
        from .models import Patient
        
        try:
            patient = Patient.objects.select_related(
                'system_hmo', 'hmo_tier',
                'hmo_enrollment_facility', 'hmo_enrollment_provider'
            ).get(id=value)
            self.context['patient'] = patient
            return value
        except Patient.DoesNotExist:
            raise serializers.ValidationError("Patient not found.")
    
    def validate(self, attrs):
        request = self.context.get('request')
        user = request.user
        patient = self.context.get('patient')
        
        if not patient:
            raise serializers.ValidationError("Patient not found.")
        
        # Patient must have an existing HMO
        if not patient.system_hmo:
            raise serializers.ValidationError({
                "patient_id": "Patient does not have an HMO enrollment to transfer."
            })
        
        # Check for existing pending approval at this facility/provider
        facility = getattr(user, 'facility', None)
        
        existing_q = PatientFacilityHMOApproval.objects.filter(
            patient=patient,
            status=PatientFacilityHMOApproval.Status.PENDING,
        )
        
        if facility:
            existing_q = existing_q.filter(facility=facility)
        else:
            existing_q = existing_q.filter(owner=user)
        
        if existing_q.exists():
            raise serializers.ValidationError({
                "patient_id": "There is already a pending HMO approval request for this patient."
            })
        
        return attrs
    
    @transaction.atomic
    def create(self, validated_data):
        request = self.context.get('request')
        user = request.user
        patient = self.context.get('patient')
        
        facility = getattr(user, 'facility', None)
        
        approval = PatientFacilityHMOApproval.objects.create(
            patient=patient,
            facility=facility if facility else None,
            owner=user if not facility else None,
            system_hmo=patient.system_hmo,
            tier=patient.hmo_tier,
            insurance_number=patient.insurance_number,
            insurance_expiry=patient.insurance_expiry,
            original_facility=patient.hmo_enrollment_facility,
            original_provider=patient.hmo_enrollment_provider,
            status=PatientFacilityHMOApproval.Status.PENDING,
            request_notes=validated_data.get('notes', ''),
        )
        
        return approval


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
