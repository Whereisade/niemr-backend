# providers/serializers.py
from django.db import transaction
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from accounts.enums import UserRole
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
from accounts.models import User
from accounts.enums import UserRole
from facilities.models import Specialty
from .models import ProviderProfile, ProviderDocument, ProviderFacilityApplication
from .enums import ProviderType, Council, VerificationStatus
from facilities.models import Specialty, Facility

# -------------------------
# Country/State Choices
# -------------------------
# Prefer a single source of truth if your project defines it.
try:
    # expected: COUNTRY_CHOICES = [("nigeria","Nigeria"), ...]
    #           NIGERIA_STATES_BY_CODE = {"Lagos": "Lagos", ...} or {"LA": "Lagos", ...}
    from core.choices import COUNTRY_CHOICES, NIGERIA_STATES_BY_CODE  # type: ignore
except Exception:
    # Fallbacks if core.choices isn't available
    COUNTRY_CHOICES = [("nigeria", "Nigeria")]
    DEFAULT_NG_STATES = [
        "Abia","Adamawa","Akwa Ibom","Anambra","Bauchi","Bayelsa","Benue","Borno",
        "Cross River","Delta","Ebonyi","Edo","Ekiti","Enugu","Gombe","Imo","Jigawa",
        "Kaduna","Kano","Katsina","Kebbi","Kogi","Kwara","Lagos","Nasarawa","Niger",
        "Ogun","Ondo","Osun","Oyo","Plateau","Rivers","Sokoto","Taraba","Yobe","Zamfara","FCT"
    ]
    NIGERIA_STATES_BY_CODE = {s: s for s in DEFAULT_NG_STATES}

User = get_user_model()


# -------------------------
# Provider Profile (read/write)
# -------------------------
class ProviderProfileSerializer(serializers.ModelSerializer):
    """
    General ProviderProfile serializer used by internal/admin endpoints.
    Supports writing specialties via a plain string list and reads them back with `specialties_read`.
    """

    # 🔹 NEW: user-facing read-only fields for the frontend table
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    role = serializers.SerializerMethodField(read_only=True)
    facility_name = serializers.CharField(
        source="user.facility.name",
        read_only=True,
    )

    # Write specialties as a simple list of strings; store as M2M to Specialty
    specialties = serializers.ListField(
        child=serializers.CharField(max_length=120),
        write_only=True,
        required=False,
    )
    specialties_read = serializers.SerializerMethodField(read_only=True)

    # Make user read-only in this serializer (created elsewhere)
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    # Read-only nested documents (avoids relying on related_name)
    documents_read = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ProviderProfile
        # `__all__` includes the new fields above as standard DRF behaviour
        fields = "__all__"

    def get_role(self, obj):
        # Map provider_type to something the UI can show as "Role"
        return obj.provider_type

    def get_specialties_read(self, obj):
        return [s.name for s in obj.specialties.all()]

    def get_documents_read(self, obj):
        qs = ProviderDocument.objects.filter(profile=obj).order_by("-uploaded_at")
        return ProviderDocumentSerializer(qs, many=True).data

    def _upsert_specialties(self, prof: ProviderProfile, names):
        if names is None:
            return
        prof.specialties.clear()
        for n in names:
            n = (n or "").strip()
            if not n:
                continue
            s, _ = Specialty.objects.get_or_create(name=n)
            prof.specialties.add(s)

    def create(self, validated_data):
        names = self.initial_data.get("specialties")
        prof = super().create(validated_data)
        self._upsert_specialties(prof, names)
        return prof

    def update(self, instance, validated_data):
        names = self.initial_data.get("specialties")
        prof = super().update(instance, validated_data)
        self._upsert_specialties(prof, names)
        return prof


# -------------------------
# Documents
# -------------------------
class ProviderDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProviderDocument
        fields = ["id", "kind", "file", "uploaded_at"]
        read_only_fields = ["id", "uploaded_at"]


# -------------------------
# Public Self-Registration (with nested documents)
# -------------------------
class SelfRegisterProviderSerializer(serializers.Serializer):
    """
    Public endpoint to create an independent provider:
      - Creates User (email is USERNAME_FIELD)
      - Creates ProviderProfile (PENDING verification)
      - Optional nested documents upload
      - Returns JWT tokens for immediate sign-in
    """

    # Account
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8, trim_whitespace=False)
    first_name = serializers.CharField(max_length=120)
    last_name = serializers.CharField(max_length=120)

    # Provider type and licensing
    provider_type = serializers.ChoiceField(choices=ProviderType.choices)
    specialties = serializers.ListField(child=serializers.CharField(max_length=120), required=False)
    license_council = serializers.ChoiceField(choices=Council.choices)
    license_number = serializers.CharField(max_length=64)
    license_expiry = serializers.DateField(required=False, allow_null=True)

    # Contact & location (Nigeria-only for now)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    country = serializers.ChoiceField(choices=COUNTRY_CHOICES, default="nigeria")
    state = serializers.ChoiceField(
        choices=[(k, v) for k, v in NIGERIA_STATES_BY_CODE.items()],
        required=False,
        allow_blank=True
    )
    lga = serializers.CharField(max_length=120, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)

    # Other profile fields
    years_experience = serializers.IntegerField(required=False, min_value=0)
    bio = serializers.CharField(required=False, allow_blank=True)
    consultation_fee = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)

    # Nested documents
    documents = ProviderDocumentSerializer(many=True, required=False)

    # ---- Validators ----
    def validate_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        # Ensure the selected state is valid for Nigeria (when provided)
        if (attrs.get("country") or "nigeria") == "nigeria" and attrs.get("state"):
            if attrs["state"] not in NIGERIA_STATES_BY_CODE:
                raise serializers.ValidationError({"state": "Select a valid Nigerian state."})
        return attrs

    @transaction.atomic
    def create(self, validated):
        documents = validated.pop("documents", [])

        # Create user
        email = validated["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError({"email": "Email is already registered."})

        # Map provider_type -> UserRole
        pt = validated["provider_type"]
        role_map = {
            "DOCTOR": UserRole.DOCTOR,
            "NURSE": UserRole.NURSE,
            "PHARMACIST": UserRole.PHARMACY,
            "LAB_SCIENTIST": UserRole.LAB,
        }
        role = role_map.get(pt, UserRole.DOCTOR)

        user = User.objects.create_user(
            email=email,
            password=validated["password"],
            first_name=validated["first_name"],
            last_name=validated["last_name"],
            role=role,
        )

        # Create profile
        prof = ProviderProfile.objects.create(
            user=user,
            provider_type=pt,
            license_council=validated["license_council"],
            license_number=validated["license_number"],
            license_expiry=validated.get("license_expiry"),
            years_experience=validated.get("years_experience") or 0,
            bio=validated.get("bio", ""),
            phone=validated.get("phone", ""),
            country=validated.get("country", "nigeria"),
            state=validated.get("state", ""),
            lga=validated.get("lga", ""),
            address=validated.get("address", ""),
            consultation_fee=validated.get("consultation_fee") or 0,
            verification_status=VerificationStatus.PENDING,
        )

        # Specialties
        for n in validated.get("specialties", []):
            name = (n or "").strip()
            if not name:
                continue
            s, _ = Specialty.objects.get_or_create(name=name)
            prof.specialties.add(s)

        # Documents
        for doc_data in documents:
            ProviderDocument.objects.create(
                profile=prof,
                kind=doc_data["kind"],
                file=doc_data["file"],
            )

        # Return tokens payload for immediate sign-in
        refresh = RefreshToken.for_user(user)
        return {
            "user_id": user.id,
            "profile_id": prof.id,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }


# -------------------------
# Facility Admin: Create Provider Directly
# -------------------------
class FacilityProviderCreateSerializer(serializers.Serializer):
    """
    Facility admin endpoint to create a provider directly linked to their facility.
    Creates User + ProviderProfile in one transaction; provider is auto-approved.
    """

    # Account fields
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8, trim_whitespace=False)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)

    # Provider type and licensing
    provider_type = serializers.ChoiceField(choices=ProviderType.choices)
    specialties = serializers.ListField(
        child=serializers.CharField(max_length=120),
        required=False,
        default=list,
    )
    license_council = serializers.ChoiceField(choices=Council.choices)
    license_number = serializers.CharField(max_length=64)
    license_expiry = serializers.DateField(required=False, allow_null=True)

    # Contact info
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)

    # Other profile fields
    years_experience = serializers.IntegerField(required=False, min_value=0, default=0)
    bio = serializers.CharField(required=False, allow_blank=True, default="")
    consultation_fee = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=0
    )

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value.lower().strip()

    def validate_password(self, value):
        validate_password(value)
        return value

    @transaction.atomic
    def create(self, validated_data):
        request = self.context.get("request")
        facility = getattr(request.user, "facility", None)

        if not facility:
            raise serializers.ValidationError(
                "You must be attached to a facility to create providers."
            )

        # Map provider_type to UserRole
        pt = validated_data["provider_type"]
        role_map = {
            "DOCTOR": UserRole.DOCTOR,
            "NURSE": UserRole.NURSE,
            "PHARMACIST": UserRole.PHARMACY,
            "LAB_SCIENTIST": UserRole.LAB,
            "DENTIST": UserRole.DOCTOR,
            "OPTOMETRIST": UserRole.DOCTOR,
            "PHYSIOTHERAPIST": UserRole.DOCTOR,
            "OTHER": UserRole.DOCTOR,
        }
        role = role_map.get(pt, UserRole.DOCTOR)

        # Create user linked to facility
        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            role=role,
            facility=facility,
            is_active=True,
        )

        # Create provider profile (auto-approved since facility admin created it)
        prof = ProviderProfile.objects.create(
            user=user,
            provider_type=pt,
            license_council=validated_data["license_council"],
            license_number=validated_data["license_number"],
            license_expiry=validated_data.get("license_expiry"),
            years_experience=validated_data.get("years_experience", 0),
            bio=validated_data.get("bio", ""),
            phone=validated_data.get("phone", ""),
            consultation_fee=validated_data.get("consultation_fee", 0),
            verification_status=VerificationStatus.APPROVED,
            verified_by=request.user,
            verified_at=timezone.now(),
        )

        # Add specialties
        for name in validated_data.get("specialties", []):
            name = (name or "").strip()
            if name:
                spec, _ = Specialty.objects.get_or_create(name=name)
                prof.specialties.add(spec)

        return {
            "user": {
                "id": user.id,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "role": user.role,
            },
            "provider": {
                "id": prof.id,
                "provider_type": prof.provider_type,
                "verification_status": prof.verification_status,
            },
            "facility": {
                "id": facility.id,
                "name": facility.name,
            },
        }


class ProviderFacilityApplicationSerializer(serializers.ModelSerializer):
    provider_name = serializers.SerializerMethodField()
    facility_name = serializers.CharField(source="facility.name", read_only=True)

    class Meta:
        model = ProviderFacilityApplication
        fields = [
            "id",
            "provider",
            "provider_name",
            "facility",
            "facility_name",
            "status",
            "message",
            "created_at",
            "decided_at",
        ]
        read_only_fields = (
            "provider",
            "status",
            "created_at",
            "decided_at",
        )

    def get_provider_name(self, obj):
        user = getattr(obj.provider, "user", None)
        if not user:
            return None
        return user.get_full_name() or user.email


class ProviderApplyToFacilitySerializer(serializers.Serializer):
    facility_id = serializers.IntegerField()
    message = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )

    def validate_facility_id(self, value):
        """
        Ensure the facility exists and stash it in the serializer context.
        """
        try:
            facility = Facility.objects.get(id=value)
        except Facility.DoesNotExist:
            raise serializers.ValidationError("Facility not found.")

        self.context["facility"] = facility
        return value

    def create(self, validated_data):
        """
        Create or update an application for the current provider to a facility.
        """
        request = self.context["request"]
        user = request.user
        facility = self.context.get("facility")

        # ---- Role guard (mirrors the view logic) ----
        provider_role_values = {
            UserRole.DOCTOR,
            UserRole.NURSE,
            UserRole.LAB,
            UserRole.PHARMACY,
        }

        if user.role not in provider_role_values:
            # This is extra safety; the main guard is in the view.
            raise serializers.ValidationError(
                "Only provider accounts can apply to facilities."
            )

        # ---- Get provider profile for this account ----
        try:
            provider = user.provider_profile
        except ProviderProfile.DoesNotExist:
            raise serializers.ValidationError(
                "You do not have a provider profile yet."
            )

        # ---- Create or update the application ----
        application, _ = ProviderFacilityApplication.objects.update_or_create(
            provider=provider,
            facility=facility,
            defaults={
                "message": validated_data.get("message", "") or "",
                "status": ProviderFacilityApplication.Status.PENDING,
                "decided_by": None,
                "decided_at": None,
            },
        )

        return application