from datetime import timedelta
from django.utils import timezone
from rest_framework import serializers

from .models import Encounter, EncounterAmendment
from .enums import EncounterStatus, EncounterType, Priority

IMMUTABLE_WINDOW_HOURS = 24

class EncounterSerializer(serializers.ModelSerializer):
    is_locked = serializers.BooleanField(read_only=True)

    class Meta:
        model = Encounter
        fields = [
            "id","patient","facility","created_by","updated_by",
            "encounter_type","status","priority","occurred_at",
            "chief_complaint","duration_value","duration_unit",
            "hpi","ros","physical_exam",
            "diagnoses","plan",
            "lab_order_ids","imaging_request_ids","prescription_ids",
            "locked_at","is_locked",
            "created_at","updated_at",
        ]
        read_only_fields = ["facility","created_by","updated_by","locked_at","is_locked","created_at","updated_at"]

    def create(self, validated):
        validated["created_by"] = self.context["request"].user
        return super().create(validated)

    def update(self, instance, validated):
        # enforce immutability
        now = timezone.now()
        if instance.is_locked or now >= (instance.created_at + timedelta(hours=IMMUTABLE_WINDOW_HOURS)):
            raise serializers.ValidationError("Encounter is locked after 24 hours. Please add an amendment instead.")
        validated["updated_by"] = self.context["request"].user
        return super().update(instance, validated)


class EncounterListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Encounter
        fields = [
            "id","patient","occurred_at","encounter_type","status","priority",
            "chief_complaint","diagnoses","plan","is_locked","created_at"
        ]


class AmendmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = EncounterAmendment
        fields = ["id","encounter","reason","content","added_by","created_at"]
        read_only_fields = ["encounter","added_by","created_at"]
