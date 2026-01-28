from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from .models import User

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    class Meta:
        model = User
        fields = ["email","password","first_name","last_name","role"]

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated):
        pwd = validated.pop("password")
        user = User.objects.create(**validated)
        user.set_password(pwd)
        user.save()
        return user

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    def validate(self, data):
        user = authenticate(email=data["email"], password=data["password"])
        if not user:
            raise serializers.ValidationError("Invalid credentials")
        data["user"] = user
        return data

class GoogleAuthSerializer(serializers.Serializer):
    id_token = serializers.CharField()

class UserProfileUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating user profile information."""
    class Meta:
        model = User
        fields = ["first_name", "last_name"]
    
    def validate_first_name(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("First name cannot be empty.")
        return value.strip()
    
    def validate_last_name(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Last name cannot be empty.")
        return value.strip()


class PasswordResetRequestSerializer(serializers.Serializer):
    """Request a password reset email."""

    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    """Confirm password reset using uid + token."""

    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_new_password(self, value):
        validate_password(value)
        return value


class PasswordChangeSerializer(serializers.Serializer):
    """Change password for an authenticated user (requires current password)."""

    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)

    def validate_new_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)

        if not user or not user.is_authenticated:
            raise serializers.ValidationError("Authentication required")

        current = attrs.get("current_password")
        if not user.check_password(current or ""):
            raise serializers.ValidationError({"current_password": "Current password is incorrect."})

        new_password = attrs.get("new_password")
        confirm = attrs.get("confirm_password")
        if new_password != confirm:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})

        if current and new_password and current == new_password:
            raise serializers.ValidationError({"new_password": "New password must be different from the current password."})

        return attrs