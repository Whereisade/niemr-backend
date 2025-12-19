from django.db import transaction
from rest_framework import serializers
from accounts.models import User
from accounts.enums import UserRole
from .models import Patient, PatientDocument, HMO, Allergy
from .enums import BloodGroup, Genotype, InsuranceStatus, AllergyType, AllergySeverity
from rest_framework import serializers as rf_serializers



class HMOSerializer(serializers.ModelSerializer):
    class Meta:
        model = HMO
        fields = ["id","name"]

class PatientSerializer(serializers.ModelSerializer):
    hmo = HMOSerializer(read_only=True)
    hmo_id = serializers.PrimaryKeyRelatedField(source="hmo", queryset=HMO.objects.all(), write_only=True, required=False, allow_null=True)

    class Meta:
        model = Patient
        fields = [
            "id","user","facility","guardian_user",
            "first_name","last_name","middle_name","dob","gender",
            "email","phone","country","state","lga","address",
            "insurance_status","hmo","hmo_id","hmo_plan",
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

# --- Dependent serializers added below ---

 # keep namespace clear if needed
# using the existing Patient model fields: dob and gender
BASIC_DEPENDENT_FIELDS = (
    "id", "first_name", "last_name", "dob", "gender",
    "parent_patient", "relationship_to_guardian", "phone",  # read-only on list/detail; set on create by the view
)

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