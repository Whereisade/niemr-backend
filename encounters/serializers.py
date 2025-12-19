# encounters/serializers.py
from datetime import timedelta

from rest_framework import serializers

from .models import Encounter, EncounterAmendment, LOCK_AFTER_HOURS
from .enums import EncounterStatus

# Clinical fields that become immutable after lock or cross-out
IMMUTABLE_FIELDS = {
    "chief_complaint",
    "duration_value",
    "duration_unit",
    "hpi",
    "ros",
    "physical_exam",
    "diagnoses",
    "plan",
    "lab_order_ids",
    "imaging_request_ids",
    "prescription_ids",
    "occurred_at",
    "priority",
    "encounter_type",
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
        fields = (
            "id",
            "patient",
            "facility",
            "occurred_at",
            "status",
            "priority",
            "encounter_type",
            "chief_complaint",
            "diagnoses",
            "plan",
            "locked",
            "created_at",
        )

    def get_locked(self, obj):
        return obj.is_locked


class EncounterSerializer(serializers.ModelSerializer):
    locked = serializers.SerializerMethodField(read_only=True)

    # ✅ FIX: do NOT set source="lock_due_at" when the field name is lock_due_at.
    # We use SerializerMethodField so it works whether lock_due_at is a @property or not.
    lock_due_at = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Encounter
        fields = "__all__"
        read_only_fields = (
            "created_by",
            "updated_by",
            "locked_at",
            "created_at",
            "updated_at",
        )

    def get_locked(self, obj):
        return obj.is_locked

    def get_lock_due_at(self, obj):
        # If your model already has a lock_due_at property, prefer it.
        existing = getattr(obj, "lock_due_at", None)
        if existing:
            return existing

        # Fallback: created_at + LOCK_AFTER_HOURS
        if not getattr(obj, "created_at", None):
            return None
        return obj.created_at + timedelta(hours=LOCK_AFTER_HOURS)

    def create(self, validated):
        req = self.context.get("request")
        if req and req.user.is_authenticated:
            validated["created_by"] = req.user
        return super().create(validated)

    def update(self, instance, validated):
        # Opportunistic lock if window elapsed
        instance.maybe_lock()

        # Hard block: if locked or crossed out, clinical payload can't change
        if instance.is_locked or instance.status == EncounterStatus.CROSSED_OUT:
            changed = _immutable_changes(instance, validated)
            if changed:
                raise serializers.ValidationError(
                    {
                        "detail": (
                            f"Encounter is immutable after {LOCK_AFTER_HOURS} hours "
                            f"or when crossed out. Blocked fields: {sorted(changed)}"
                        )
                    }
                )

        # allow status transitions even post-lock
        req = self.context.get("request")
        if req and req.user.is_authenticated:
            validated["updated_by"] = req.user
        return super().update(instance, validated)


class AmendmentSerializer(serializers.ModelSerializer):
    """
    Append-only: create allowed; updates/deletes are blocked in the view.
    """

    added_by_name = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()

    class Meta:
        model = EncounterAmendment
        fields = (
            "id",
            "encounter",
            "section",
            "added_by",
            "added_by_name",
            "reason",
            "content",
            "created_at",
            "attachments",
        )
        read_only_fields = ("added_by", "created_at")

    def create(self, validated):
        req = self.context.get("request")
        if req and req.user.is_authenticated:
            validated["added_by"] = req.user
        return super().create(validated)

    def get_added_by_name(self, obj):
        u = getattr(obj, "added_by", None)
        if not u:
            return None
        name = (getattr(u, "get_full_name", lambda: "")() or "").strip()
        if name:
            return name
        return getattr(u, "username", None) or getattr(u, "email", None)

    def get_attachments(self, obj):
        # The view can inject a pre-serialized mapping for efficiency:
        # { amendment_id: [FileSerializer(...).data, ...] }
        m = self.context.get("attachments_map") or {}
        return m.get(obj.id, [])
