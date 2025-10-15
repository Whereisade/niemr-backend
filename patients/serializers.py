from django.db import transaction
from rest_framework import serializers
from accounts.models import User
from accounts.enums import UserRole
from .models import Patient, PatientDocument, HMO
from .enums import BloodGroup, Genotype, InsuranceStatus

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
        email = validated["email"]
        password = validated["password"]
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        user = User.objects.create(email=email, username=email.split("@")[0], role=UserRole.PATIENT)
        user.set_password(password)
        user.save()

        # 2) Create Patient linked to user
        p_fields = {k: v for k, v in validated.items() if k not in ("email","password")}
        patient = Patient.objects.create(user=user, **p_fields)
        return patient

class PatientDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientDocument
        fields = ["id","doc_type","file","uploaded_at"]
