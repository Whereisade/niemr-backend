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
