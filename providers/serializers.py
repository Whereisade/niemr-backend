from django.db import transaction
from rest_framework import serializers
from accounts.models import User
from accounts.enums import UserRole
from facilities.models import Specialty
from .models import ProviderProfile, ProviderDocument
from .enums import ProviderType, Council, VerificationStatus

class ProviderProfileSerializer(serializers.ModelSerializer):
    specialties = serializers.ListField(
        child=serializers.CharField(max_length=120), write_only=True, required=False
    )
    specialties_read = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ProviderProfile
        fields = [
            "id","user","provider_type","specialties","specialties_read",
            "license_council","license_number","license_expiry",
            "years_experience","bio",
            "phone","country","state","lga","address",
            "consultation_fee",
            "verification_status","verified_at","verified_by","rejection_reason",
            "created_at","updated_at",
        ]
        read_only_fields = ["user","verification_status","verified_at","verified_by","rejection_reason","created_at","updated_at"]

    def get_specialties_read(self, obj):
        return [s.name for s in obj.specialties.all()]

    def _upsert_specialties(self, profile, names):
        if not names:
            return
        specs = []
        for n in names:
            s, _ = Specialty.objects.get_or_create(name=n.strip())
            specs.append(s)
        profile.specialties.set(specs)

    def create(self, validated):
        # only used by admin-created profiles; self-register uses SelfRegisterProviderSerializer
        prof = ProviderProfile.objects.create(**validated)
        self._upsert_specialties(prof, self.initial_data.get("specialties"))
        return prof

    def update(self, instance, validated):
        prof = super().update(instance, validated)
        if "specialties" in self.initial_data:
            self._upsert_specialties(prof, self.initial_data.get("specialties"))
        return prof

class SelfRegisterProviderSerializer(serializers.Serializer):
    """
    Public endpoint to create an independent provider:
    - User (role set from provider_type; no facility)
    - ProviderProfile (PENDING verification)
    """
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=120)
    last_name = serializers.CharField(max_length=120)

    provider_type = serializers.ChoiceField(choices=ProviderType.choices)
    specialties = serializers.ListField(child=serializers.CharField(max_length=120), required=False)
    license_council = serializers.ChoiceField(choices=Council.choices)
    license_number = serializers.CharField(max_length=64)
    license_expiry = serializers.DateField(required=False, allow_null=True)

    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    country = serializers.CharField(max_length=120, required=False, allow_blank=True)
    state = serializers.CharField(max_length=120, required=False, allow_blank=True)
    lga = serializers.CharField(max_length=120, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    years_experience = serializers.IntegerField(required=False, min_value=0)
    bio = serializers.CharField(required=False, allow_blank=True)
    consultation_fee = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)

    @transaction.atomic
    def create(self, validated):
        email = validated["email"]
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        # role mapping: prefer specific roles for RBAC
        pt = validated["provider_type"]
        role_map = {
            "DOCTOR": UserRole.DOCTOR,
            "NURSE": UserRole.NURSE,
            "PHARMACIST": UserRole.PHARMACY,
            "LAB_SCIENTIST": UserRole.LAB,
        }
        role = role_map.get(pt, UserRole.DOCTOR)

        user = User.objects.create(
            email=email,
            username=email.split("@")[0],
            role=role,
            first_name=validated["first_name"],
            last_name=validated["last_name"],
        )
        user.set_password(validated["password"]); user.save()

        prof = ProviderProfile.objects.create(
            user=user,
            provider_type=pt,
            license_council=validated["license_council"],
            license_number=validated["license_number"],
            license_expiry=validated.get("license_expiry"),
            years_experience=validated.get("years_experience") or 0,
            bio=validated.get("bio",""),
            phone=validated.get("phone",""),
            country=validated.get("country",""),
            state=validated.get("state",""),
            lga=validated.get("lga",""),
            address=validated.get("address",""),
            consultation_fee=validated.get("consultation_fee") or 0,
            verification_status=VerificationStatus.PENDING,
        )
        # specialties
        for n in validated.get("specialties", []):
            s, _ = Specialty.objects.get_or_create(name=n.strip())
            prof.specialties.add(s)
        return prof

class ProviderDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProviderDocument
        fields = ["id","kind","file","uploaded_at"]
