from rest_framework import serializers
from .models import Facility, Specialty, Ward, Bed, FacilityExtraDocument
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
        fields = ["id","name"]

class WardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ward
        fields = ["id","name","capacity"]

class BedSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bed
        fields = ["id","number","is_available","ward"]

class FacilityExtraDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = FacilityExtraDocument
        fields = ["id","title","file","uploaded_at"]

class FacilityCreateSerializer(serializers.ModelSerializer):
    specialties = serializers.ListField(
        child=serializers.CharField(max_length=120), write_only=True, required=False
    )

    class Meta:
        model = Facility
        fields = [
            "id","facility_type","name","controlled_by","country","state","lga","address",
            "email","registration_number","phone","nhis_approved","nhis_number",
            "total_bed_capacity","specialties",
            "nhis_certificate","md_practice_license","state_registration_cert",
        ]

    def validate(self, attrs):
        country = attrs.get("country")
        state = attrs.get("state")
        # block group sentinel options
        if state and state.startswith("---"):
            raise serializers.ValidationError({"state": "Select a valid state/region, not a group header."})

        # ensure state belongs to chosen country when provided
        if state:
            country_state_map = {
                "nigeria": [k for k, _ in Facility.NIGERIA_STATES],
                "ghana": [k for k, _ in Facility.GHANA_REGIONS],
                "kenya": [k for k, _ in Facility.KENYA_COUNTIES],
                "south_afica": [k for k, _ in Facility.SOUTH_AFRICA_PROVINCES],  # note key should match COUNTRY_CHOICES value
                # if your COUNTRY_CHOICES uses 'south_africa' use that key instead
            }
            # normalize country key used in COUNTRY_CHOICES
            country_key = country
            if country_key not in country_state_map and country_key == "south_africa":
                country_key = "south_afica"  # adjust if needed

            allowed = country_state_map.get(country_key)
            if allowed is not None and state not in allowed:
                raise serializers.ValidationError({"state": "Selected state/region does not match the chosen country."})

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
            "id","facility_type","name","controlled_by","country","state","lga","address",
            "email","registration_number","phone","nhis_approved","nhis_number",
            "total_bed_capacity","specialties","wards","extra_docs",
            "nhis_certificate","md_practice_license","state_registration_cert",
            "is_active","created_at","updated_at",
        ]

# --- NIEMR: Public signup serializer to create Facility + Super Admin (with password) ---
class FacilityAdminSignupSerializer(serializers.Serializer):
    """Create Facility and the first SUPER_ADMIN user atomically, then return JWTs."""
    # Admin fields
    admin_email = serializers.EmailField()
    admin_password = serializers.CharField(write_only=True, min_length=8)
    admin_first_name = serializers.CharField(max_length=150)
    admin_last_name = serializers.CharField(max_length=150)
    admin_phone = serializers.CharField(max_length=50, required=False, allow_blank=True)

    # Facility fields (align with models.Facility)
    name = serializers.CharField(max_length=255)
    facility_type = serializers.CharField(max_length=32, required=False, allow_blank=True)
    controlled_by = serializers.CharField(max_length=32, required=False, allow_blank=True)
    country = serializers.CharField(max_length=64)
    state = serializers.CharField(max_length=64, required=False, allow_blank=True)
    lga = serializers.CharField(max_length=64, required=False, allow_blank=True)
    city = serializers.CharField(max_length=64, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True, max_length=500)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    registration_number = serializers.CharField(required=False, allow_blank=True, max_length=120)
    nhis_approved = serializers.BooleanField(required=False)
    nhis_number = serializers.CharField(required=False, allow_blank=True, max_length=120)

    # optional single specialty id or list of names
    specialty = serializers.PrimaryKeyRelatedField(queryset=Specialty.objects.all(), required=False, allow_null=True)
    specialties = serializers.ListField(
        child=serializers.CharField(max_length=120), write_only=True, required=False
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
        User = get_user_model()

        # normalize and extract admin fields
        admin_email = validated_data.pop("admin_email").strip().lower()
        admin_password = validated_data.pop("admin_password")
        admin_first_name = validated_data.pop("admin_first_name", "").strip()
        admin_last_name = validated_data.pop("admin_last_name", "").strip()
        admin_phone = validated_data.pop("admin_phone", "").strip()

        specialty_pk = validated_data.pop("specialty", None)
        specialties_names = validated_data.pop("specialties", None)

        # fail fast if email already taken
        if User.objects.filter(email__iexact=admin_email).exists():
            raise serializers.ValidationError({"admin_email": "Email is already registered."})

        # create facility
        facility = Facility.objects.create(**validated_data)

        # specialties handling
        if specialty_pk:
            facility.specialties.add(specialty_pk)
        if specialties_names:
            for name in specialties_names:
                sp, _ = Specialty.objects.get_or_create(name=name.strip())
                facility.specialties.add(sp)

        # Create user using email as the USERNAME_FIELD (pass email as first positional arg)
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

        # tokens
        refresh = RefreshToken.for_user(user)
        return {
            "facility": {"id": str(getattr(facility, "id", "")), "name": facility.name},
            "user": {
                "id": str(getattr(user, "id", "")),
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "role": getattr(user, "role", None),
            },
            "tokens": {"refresh": str(refresh), "access": str(refresh.access_token)},
        }
