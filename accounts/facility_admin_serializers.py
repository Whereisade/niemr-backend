# accounts/facility_admin_serializers.py
"""
Serializers for facility admin management.
Only SUPER_ADMIN can create/manage ADMIN and FRONTDESK users.
"""
from django.db import transaction
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from rest_framework import serializers
from .enums import UserRole

User = get_user_model()


class FacilityStaffCreateSerializer(serializers.Serializer):
    """
    SUPER_ADMIN endpoint to create staff accounts (ADMIN, FRONTDESK) 
    directly linked to their facility.
    """

    # Account fields
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8, trim_whitespace=False)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)

    # Role - restricted to non-clinical staff roles
    role = serializers.ChoiceField(
        choices=[
            (UserRole.ADMIN, "Admin"),
            (UserRole.FRONTDESK, "Front Desk"),
        ]
    )

    # Optional contact
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)

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
                "You must be attached to a facility to create staff."
            )

        # Create user linked to facility
        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            role=validated_data["role"],
            facility=facility,
            is_active=True,
            is_staff=validated_data["role"] == UserRole.ADMIN,  # Admins get staff access
        )

        return {
            "user": {
                "id": user.id,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "role": user.role,
            },
            "facility": {
                "id": facility.id,
                "name": facility.name,
            },
        }


class FacilityStaffListSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for listing facility staff (ADMIN, FRONTDESK).
    """
    facility_name = serializers.CharField(source="facility.name", read_only=True)
    role_display = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "role",
            "role_display",
            "is_active",
            "facility",
            "facility_name",
            "date_joined",
            "last_login",
        ]
        read_only_fields = fields

    def get_role_display(self, obj):
        return obj.get_role_display() if hasattr(obj, 'get_role_display') else obj.role


class FacilityStaffUpdateSerializer(serializers.ModelSerializer):
    """
    Update serializer for facility staff - limited fields.
    """
    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "is_active",
        ]

    def validate(self, attrs):
        # Prevent deactivating yourself
        request = self.context.get("request")
        if request and self.instance:
            if self.instance.id == request.user.id and attrs.get("is_active") is False:
                raise serializers.ValidationError(
                    {"is_active": "You cannot deactivate your own account."}
                )
        return attrs