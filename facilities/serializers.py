from rest_framework import serializers
from .models import Facility, Specialty, Ward, Bed, FacilityExtraDocument

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

    def create(self, validated):
        spec_names = validated.pop("specialties", [])
        facility = Facility.objects.create(**validated)
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
