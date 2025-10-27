from rest_framework import serializers
from .models import Encounter, EncounterAmendment, LOCK_AFTER_HOURS
from .enums import EncounterStatus

# Clinical payload that becomes immutable post-lock
IMMUTABLE_FIELDS = {
    "chief_complaint", "duration_value", "duration_unit",
    "hpi", "ros", "physical_exam",
    "diagnoses", "plan",
    "lab_order_ids", "imaging_request_ids", "prescription_ids",
    "occurred_at", "priority", "encounter_type",
}

def _immutable_changes(instance: Encounter, incoming: dict) -> set:
    changed = set()
    for f in IMMUTABLE_FIELDS:
        if f in incoming and getattr(instance, f) != incoming[f]:
            changed.add(f)
    return changed

class EncounterListSerializer(serializers.ModelSerializer):
    locked = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Encounter
        fields = ("id", "patient", "facility", "occurred_at", "status", "priority", "encounter_type",
                  "chief_complaint", "diagnoses", "plan", "locked", "created_at")

    def get_locked(self, obj):
        return obj.is_locked

class EncounterSerializer(serializers.ModelSerializer):
    locked = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Encounter
        fields = "__all__"
        read_only_fields = ("created_by","updated_by","locked_at","created_at","updated_at")

    def get_locked(self, obj):
        return obj.is_locked

    def create(self, validated):
        req = self.context.get("request")
        if req and req.user.is_authenticated:
            validated["created_by"] = req.user
        return super().create(validated)

    def update(self, instance, validated):
        # opportunistically lock if elapsed
        instance.maybe_lock()

        # Hard block: if locked or crossed out, clinical fields cannot change
        if instance.is_locked or instance.status == EncounterStatus.CROSSED_OUT:
            changed = _immutable_changes(instance, validated)
            if changed:
                raise serializers.ValidationError({
                    "detail": f"Encounter becomes immutable after {LOCK_AFTER_HOURS} hours "
                              f"or when crossed out. Blocked fields: {sorted(changed)}"
                })

        # Soft allow: we still permit status updates (e.g., OPEN→CLOSED) even after lock
        req = self.context.get("request")
        if req and req.user.is_authenticated:
            validated["updated_by"] = req.user
        return super().update(instance, validated)

class AmendmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = EncounterAmendment
        fields = ("id","encounter","added_by","reason","content","created_at")
        read_only_fields = ("added_by","created_at")

    def create(self, validated):
        req = self.context.get("request")
        if req and req.user.is_authenticated:
            validated["added_by"] = req.user
        return super().create(validated)
