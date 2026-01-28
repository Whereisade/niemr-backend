from rest_framework import serializers
from django.contrib.auth import get_user_model

from facilities.models import Facility
from facilities.serializers import FacilityDetailSerializer

User = get_user_model()


class FacilityAdminListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Facility
        fields = [
            "id",
            "name",
            "email",
            "state",
            "lga",
            "facility_type",
            "is_active",
            "is_publicly_visible",
            "created_at",
            "updated_at",
        ]


class FacilityAdminDetailSerializer(FacilityDetailSerializer):
    """
    Reuse FacilityDetailSerializer but add is_publicly_visible (not included in the base detail serializer).
    """
    is_publicly_visible = serializers.BooleanField(read_only=False)

    class Meta(FacilityDetailSerializer.Meta):
        fields = list(FacilityDetailSerializer.Meta.fields) + ["is_publicly_visible"]


class UserAdminSerializer(serializers.ModelSerializer):
    facility_name = serializers.CharField(source="facility.name", read_only=True)
    has_provider_profile = serializers.SerializerMethodField()
    has_patient_profile = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "role",
            "is_active",
            "is_staff",
            "is_superuser",
            "email_verified",
            "facility",
            "facility_name",
            "created_by_facility",
            "is_sacked",
            "sacked_at",
            "date_joined",
            "last_login",
            "has_provider_profile",
            "has_patient_profile",
        ]

    def get_has_provider_profile(self, obj):
        return hasattr(obj, "provider_profile")

    def get_has_patient_profile(self, obj):
        return hasattr(obj, "patient_profile")
