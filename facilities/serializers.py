from rest_framework import serializers
from patients.models import Patient, HMO
from encounters.models import Encounter
from .models import (
    Facility,
    Specialty,
    Ward,
    Bed,
    FacilityExtraDocument,
    BedAssignment,
)
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils.text import slugify
from accounts.models import UserRole


class SpecialtySerializer(serializers.ModelSerializer):
    class Meta:
        model = Specialty
        fields = ["id", "name"]


class BedSerializer(serializers.ModelSerializer):
    current_assignment = serializers.SerializerMethodField()

    class Meta:
        model = Bed
        fields = [
            "id",
            "number",
            "bed_class",
            "status",
            "has_oxygen",
            "has_monitor",
            "is_operational",
            "notes",
            "ward",
            "is_available",          # keep for compatibility
            "current_assignment",    # 🆕 who is in the bed (if any)
        ]
        read_only_fields = ["id", "current_assignment"]

    def get_current_assignment(self, obj):
        """
        Returns the active bed assignment (if any) with a small patient snippet.
        """
        assignment = (
            obj.assignments.filter(discharged_at__isnull=True)
            .select_related("patient", "encounter")
            .first()
        )
        if not assignment:
            return None

        patient = assignment.patient
        encounter = assignment.encounter

        display_name = (
            " ".join(
                p for p in [patient.first_name, patient.last_name] if p
            )
            or getattr(patient, "hospital_number", None)
            or f"Patient #{patient.id}"
        )

        return {
            "id": assignment.id,
            "patient": {
                "id": patient.id,
                "first_name": patient.first_name,
                "last_name": patient.last_name,
                "hospital_number": getattr(patient, "hospital_number", None),
                "display_name": display_name,
            },
            "encounter": encounter.id if encounter else None,
            "assigned_at": assignment.assigned_at,
        }


class BedAssignmentSerializer(serializers.ModelSerializer):
    bed = serializers.PrimaryKeyRelatedField(
        queryset=Bed.objects.select_related("ward", "ward__facility")
    )
    patient = serializers.PrimaryKeyRelatedField(queryset=Patient.objects.all())
    encounter = serializers.PrimaryKeyRelatedField(
        queryset=Encounter.objects.all(),
        required=False,
        allow_null=True,
    )
    is_active = serializers.SerializerMethodField(read_only=True)
    ward = serializers.SerializerMethodField(read_only=True)
    facility = serializers.SerializerMethodField(read_only=True)
    status = serializers.SerializerMethodField(read_only=True)
    patient_display = serializers.SerializerMethodField(read_only=True)
    bed_display = serializers.SerializerMethodField(read_only=True)
    # 🆕 NEW FIELDS for provider names
    assigned_by_name = serializers.SerializerMethodField(read_only=True)
    discharged_by_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = BedAssignment
        fields = [
            "id",
            "bed",
            "bed_display",
            "patient",
            "patient_display",
            "encounter",
            "assigned_at",
            "discharged_at",
            "assigned_by",
            "assigned_by_name",  
            "discharged_by",
            "discharged_by_name",  
            "notes",
            "is_active",
            "status",
            "ward",
            "facility",
        ]
        read_only_fields = [
            "id",
            "assigned_at",
            "discharged_at",
            "assigned_by",
            "assigned_by_name",  # 🆕 NEW
            "discharged_by",
            "discharged_by_name",  # 🆕 NEW
            "is_active",
            "status",
            "ward",
            "facility",
            "patient_display",
            "bed_display",
        ]

    def get_is_active(self, obj):
        return obj.is_active

    def get_status(self, obj):
        return "ACTIVE" if obj.discharged_at is None else "DISCHARGED"

    def get_ward(self, obj):
        w = obj.bed.ward
        return {"id": w.id, "name": w.name}

    def get_facility(self, obj):
        f = obj.bed.ward.facility
        return {"id": f.id, "name": f.name}

    def get_patient_display(self, obj):
        p = obj.patient
        name = " ".join(filter(None, [p.first_name, p.last_name]))
        return name or getattr(p, "hospital_number", None) or f"Patient #{p.id}"

    def get_bed_display(self, obj):
        b = obj.bed
        return f"{b.ward.name} – {b.number}"

    # 🆕 NEW METHOD: Get the name of the user who assigned this bed
    def get_assigned_by_name(self, obj):
        """Return the name of the user who assigned this bed"""
        if not obj.assigned_by:
            return None
        user = obj.assigned_by
        name = " ".join(filter(None, [user.first_name, user.last_name]))
        return name or user.email or f"User #{user.id}"

    # 🆕 NEW METHOD: Get the name of the user who discharged this bed
    def get_discharged_by_name(self, obj):
        """Return the name of the user who discharged this bed"""
        if not obj.discharged_by:
            return None
        user = obj.discharged_by
        name = " ".join(filter(None, [user.first_name, user.last_name]))
        return name or user.email or f"User #{user.id}"

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)

        bed = attrs["bed"]
        patient = attrs["patient"]

        # Facility scoping
        if user and getattr(user, "facility_id", None):
            if bed.ward.facility_id != user.facility_id:
                raise serializers.ValidationError(
                    "You can only assign beds within your facility."
                )

        # Bed operational status
        if hasattr(bed, "is_operational") and not bed.is_operational:
            raise serializers.ValidationError(
                "Selected bed is not operational."
            )

        # Optional status-based restriction
        if hasattr(bed, "status") and bed.status in (
            Bed.BedStatus.OUT_OF_SERVICE,
            Bed.BedStatus.CLEANING,
        ):
            raise serializers.ValidationError(
                "Selected bed is not available for assignment."
            )

        # Ensure no active assignment on this bed
        if bed.assignments.filter(discharged_at__isnull=True).exists():
            raise serializers.ValidationError(
                "This bed already has an active assignment."
            )

        # 🆕 CRITICAL: Prevent patient from having multiple active bed assignments
        # A patient should only be in ONE bed at a time
        existing_assignment = BedAssignment.objects.filter(
            patient=patient,
            discharged_at__isnull=True
        ).select_related("bed", "bed__ward").first()

        if existing_assignment:
            bed_info = f"{existing_assignment.bed.ward.name}, Bed {existing_assignment.bed.number}"
            raise serializers.ValidationError(
                f"Patient already has an active bed assignment in {bed_info}. "
                f"Please discharge them first before assigning to a new bed."
            )

        # Optional: validate patient facility if your model has it
        if getattr(patient, "facility_id", None) and getattr(
            user, "facility_id", None
        ):
            if patient.facility_id != user.facility_id:
                raise serializers.ValidationError(
                    "Patient does not belong to this facility."
                )

        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            validated_data["assigned_by"] = user
        return super().create(validated_data)


class WardSerializer(serializers.ModelSerializer):
    beds = BedSerializer(many=True, read_only=True)

    class Meta:
        model = Ward
        fields = [
            "id",
            "name",
            "capacity",
            "ward_type",
            "gender_policy",
            "floor",
            "is_active",
            "beds",
        ]
        read_only_fields = ["id"]


class FacilityExtraDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = FacilityExtraDocument
        fields = ["id", "title", "file", "uploaded_at"]


class FacilityExtraDocumentInlineSerializer(serializers.ModelSerializer):
    class Meta:
        model = FacilityExtraDocument
        fields = ["title", "file"]


class FacilityCreateSerializer(serializers.ModelSerializer):
    specialties = serializers.ListField(
        child=serializers.CharField(max_length=120),
        write_only=True,
        required=False,
    )

    class Meta:
        model = Facility
        fields = [
            "id",
            "facility_type",
            "name",
            "controlled_by",
            "country",
            "state",
            "lga",
            "address",
            "email",
            "registration_number",
            "phone",
            "nhis_approved",
            "nhis_number",
            "total_bed_capacity",
            "specialties",
            "nhis_certificate",
            "md_practice_license",
            "state_registration_cert",
        ]

    def validate(self, attrs):
        country = attrs.get("country")
        state = attrs.get("state")
        # block group sentinel options
        if state and state.startswith("---"):
            raise serializers.ValidationError(
                {"state": "Select a valid state/region, not a group header."}
            )

        # ensure state belongs to chosen country when provided
        if state:
            country_state_map = {
                "nigeria": [k for k, _ in Facility.NIGERIA_STATES],
                "ghana": [k for k, _ in Facility.GHANA_REGIONS],
                "kenya": [k for k, _ in Facility.KENYA_COUNTIES],
                "south_afica": [
                    k for k, _ in Facility.SOUTH_AFRICA_PROVINCES
                ],  # note key should match COUNTRY_CHOICES value
                # if your COUNTRY_CHOICES uses 'south_africa' use that key instead
            }
            # normalize country key used in COUNTRY_CHOICES
            country_key = country
            if (
                country_key not in country_state_map
                and country_key == "south_africa"
            ):
                country_key = "south_afica"  # adjust if needed

            allowed = country_state_map.get(country_key)
            if allowed is not None and state not in allowed:
                raise serializers.ValidationError(
                    {
                        "state": "Selected state/region does not match the chosen country."
                    }
                )

        return attrs

    def create(self, validated_data):
        spec_names = validated_data.pop("specialties", [])
        facility = Facility.objects.create(**validated_data)
        if spec_names:
            specs = []
            for n in spec_names:
                s, _ = Specialty.objects.get_or_create(name=n.strip())
                specs.append(s)
            facility.specialties.set(specs)
        return facility


class FacilityDetailSerializer(serializers.ModelSerializer):
    specialties = SpecialtySerializer(many=True, read_only=True)
    wards = WardSerializer(many=True, read_only=True)
    extra_docs = FacilityExtraDocumentSerializer(many=True, read_only=True)

    class Meta:
        model = Facility
        fields = [
            "id",
            "facility_type",
            "name",
            "controlled_by",
            "country",
            "state",
            "lga",
            "address",
            "email",
            "registration_number",
            "phone",
            "nhis_approved",
            "nhis_number",
            "total_bed_capacity",
            "specialties",
            "wards",
            "extra_docs",
            "nhis_certificate",
            "md_practice_license",
            "state_registration_cert",
            "is_active",
            "created_at",
            "updated_at",
        ]


# --- NIEMR: Public signup serializer to create Facility + Super Admin (with password) ---
class FacilityAdminSignupSerializer(serializers.Serializer):
    """Create Facility and the first SUPER_ADMIN user atomically, then return JWTs."""

    # Admin fields
    admin_email = serializers.EmailField()
    admin_password = serializers.CharField(write_only=True, min_length=8)
    admin_first_name = serializers.CharField(max_length=150)
    admin_last_name = serializers.CharField(max_length=150)
    admin_phone = serializers.CharField(
        max_length=50, required=False, allow_blank=True
    )

    # Facility fields (align with models.Facility)
    name = serializers.CharField(max_length=255)
    facility_type = serializers.CharField(
        max_length=32, required=False, allow_blank=True
    )
    controlled_by = serializers.CharField(
        max_length=32, required=False, allow_blank=True
    )
    country = serializers.CharField(max_length=64)
    state = serializers.CharField(
        max_length=64, required=False, allow_blank=True
    )
    lga = serializers.CharField(
        max_length=64, required=False, allow_blank=True
    )
    city = serializers.CharField(
        max_length=64, required=False, allow_blank=True
    )
    address = serializers.CharField(
        required=False, allow_blank=True, max_length=500
    )
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(
        required=False, allow_blank=True, max_length=32
    )
    registration_number = serializers.CharField(
        required=False, allow_blank=True, max_length=120
    )
    nhis_approved = serializers.BooleanField(required=False)
    nhis_number = serializers.CharField(
        required=False, allow_blank=True, max_length=120
    )

    # Document fields
    nhis_certificate = serializers.FileField(
        required=False, allow_null=True
    )
    md_practice_license = serializers.FileField(
        required=False, allow_null=True
    )
    state_registration_cert = serializers.FileField(
        required=False, allow_null=True
    )

    # Extra documents
    extra_documents = FacilityExtraDocumentInlineSerializer(
        many=True, required=False
    )

    # Keep existing specialty fields
    specialty = serializers.PrimaryKeyRelatedField(
        queryset=Specialty.objects.all(), required=False, allow_null=True
    )
    specialties = serializers.ListField(
        child=serializers.CharField(max_length=120),
        write_only=True,
        required=False,
    )

    def validate_admin_email(self, value):
        User = get_user_model()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Email is already in use.")
        return value

    def validate_admin_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(list(e.messages))
        return value

    @transaction.atomic
    def create(self, validated_data):
        # Extract nested data
        extra_docs = validated_data.pop("extra_documents", [])
        admin_email = validated_data.pop("admin_email").strip().lower()
        admin_password = validated_data.pop("admin_password")
        admin_first_name = validated_data.pop(
            "admin_first_name", ""
        ).strip()
        admin_last_name = validated_data.pop(
            "admin_last_name", ""
        ).strip()
        admin_phone = validated_data.pop("admin_phone", "").strip()
        specialty_pk = validated_data.pop("specialty", None)
        specialties_names = validated_data.pop("specialties", None)

        # Create facility with document fields
        facility = Facility.objects.create(**validated_data)

        # Handle specialties (keep existing logic)
        if specialty_pk:
            facility.specialties.add(specialty_pk)
        if specialties_names:
            for name in specialties_names:
                sp, _ = Specialty.objects.get_or_create(name=name.strip())
                facility.specialties.add(sp)

        # Create extra documents
        for doc in extra_docs:
            FacilityExtraDocument.objects.create(
                facility=facility,
                title=doc["title"],
                file=doc["file"],
            )

        # Create admin user (keep existing logic)
        User = get_user_model()
        user = User.objects.create_user(
            admin_email,
            password=admin_password,
            first_name=admin_first_name,
            last_name=admin_last_name,
            role=getattr(UserRole, "SUPER_ADMIN", None),
            facility=facility,
            is_active=True,
            is_staff=True,
        )
        if hasattr(user, "phone") and admin_phone:
            user.phone = admin_phone
            user.save()

        # Generate tokens (keep existing logic)
        refresh = RefreshToken.for_user(user)
        return {
            "facility": {
                "id": str(getattr(facility, "id", "")),
                "name": facility.name,
            },
            "user": {
                "id": str(getattr(user, "id", "")),
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "role": getattr(user, "role", None),
            },
            "tokens": {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
            },
        }




class FacilityHMOSerializer(serializers.ModelSerializer):
    class Meta:
        model = HMO
        fields = ["id", "name", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]
