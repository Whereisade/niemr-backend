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
