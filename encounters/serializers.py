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


def _get_user_name(user):
    """Helper to get full name from user object"""
    if not user:
        return None
    first = getattr(user, "first_name", "") or ""
    last = getattr(user, "last_name", "") or ""
    full = f"{first} {last}".strip()
    return full if full else getattr(user, "email", None)


class EncounterListSerializer(serializers.ModelSerializer):
    locked = serializers.SerializerMethodField(read_only=True)

    # Computed name fields for frontend display
    patient_name = serializers.SerializerMethodField(read_only=True)
    facility_name = serializers.SerializerMethodField(read_only=True)
    
    # NEW: Distinguish between nurse and provider
    nurse_name = serializers.SerializerMethodField(read_only=True)
    provider_name = serializers.SerializerMethodField(read_only=True)
    provider_is_independent = serializers.SerializerMethodField(read_only=True)
    created_by_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Encounter
        fields = (
            "id",
            "patient",
            "patient_name",
            "facility",
            "facility_name",
            "created_by",
            "created_by_name",
            "nurse",
            "nurse_name",
            "provider",
            "provider_name",
            "provider_is_independent",
            "occurred_at",
            "status",
            "stage",
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

    def get_patient_name(self, obj):
        if not obj.patient:
            return None
        first = getattr(obj.patient, "first_name", "") or ""
        last = getattr(obj.patient, "last_name", "") or ""
        full = f"{first} {last}".strip()
        return full if full else None

    def _is_independent_provider(self, provider):
        # Independent provider = has provider_profile AND no facility_id
        if not provider:
            return False
        if getattr(provider, "facility_id", None):
            return False
        return getattr(provider, "provider_profile", None) is not None

    def get_provider_is_independent(self, obj):
        return self._is_independent_provider(getattr(obj, "provider", None))


    def get_facility_name(self, obj):
        req = self.context.get("request")
        if req and getattr(req.user, "role", None) == "PATIENT" and self._is_independent_provider(getattr(obj, "provider", None)):
            return None
        if not obj.facility:
            return None
        return getattr(obj.facility, "name", None)

    def get_nurse_name(self, obj):
        """Return nurse's full name if set"""
        return _get_user_name(obj.nurse)

    def get_provider_name(self, obj):
        """Return provider's (doctor's) full name if set"""
        return _get_user_name(obj.provider)

    def get_created_by_name(self, obj):
        """Return creator's full name"""
        return _get_user_name(obj.created_by)


class EncounterSerializer(serializers.ModelSerializer):
    locked = serializers.SerializerMethodField(read_only=True)
    lock_due_at = serializers.SerializerMethodField(read_only=True)

    # Computed name fields for frontend display
    patient_name = serializers.SerializerMethodField(read_only=True)
    patient_first_name = serializers.SerializerMethodField(read_only=True)
    patient_last_name = serializers.SerializerMethodField(read_only=True)
    facility_name = serializers.SerializerMethodField(read_only=True)
    
    # NEW: Separate nurse and provider
    nurse_name = serializers.SerializerMethodField(read_only=True)
    nurse_first_name = serializers.SerializerMethodField(read_only=True)
    nurse_last_name = serializers.SerializerMethodField(read_only=True)
    
    provider_name = serializers.SerializerMethodField(read_only=True)
    provider_first_name = serializers.SerializerMethodField(read_only=True)
    provider_last_name = serializers.SerializerMethodField(read_only=True)
    
    created_by_name = serializers.SerializerMethodField(read_only=True)
    created_by_first_name = serializers.SerializerMethodField(read_only=True)
    created_by_last_name = serializers.SerializerMethodField(read_only=True)
    
    # Timeline event names
    paused_by_name = serializers.SerializerMethodField(read_only=True)
    resumed_by_name = serializers.SerializerMethodField(read_only=True)
    labs_skipped_by_name = serializers.SerializerMethodField(read_only=True)
    clinical_finalized_by_name = serializers.SerializerMethodField(read_only=True)

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
        existing = getattr(obj, "lock_due_at", None)
        if existing:
            return existing
        if not getattr(obj, "created_at", None):
            return None
        return obj.created_at + timedelta(hours=LOCK_AFTER_HOURS)

    # Patient fields
    def get_patient_name(self, obj):
        if not obj.patient:
            return None
        first = getattr(obj.patient, "first_name", "") or ""
        last = getattr(obj.patient, "last_name", "") or ""
        full = f"{first} {last}".strip()
        return full if full else None

    def get_patient_first_name(self, obj):
        if not obj.patient:
            return None
        return getattr(obj.patient, "first_name", None)

    def get_patient_last_name(self, obj):
        if not obj.patient:
            return None
        return getattr(obj.patient, "last_name", None)

    # Facility fields

    def get_facility_name(self, obj):
        req = self.context.get("request")
        provider = getattr(obj, "provider", None)
        if req and getattr(req.user, "role", None) == "PATIENT":
            if provider and not getattr(provider, "facility_id", None) and getattr(provider, "provider_profile", None) is not None:
                return None
        if not obj.facility:
            return None
        return getattr(obj.facility, "name", None)

    # Nurse fields
    def get_nurse_name(self, obj):
        return _get_user_name(obj.nurse)

    def get_nurse_first_name(self, obj):
        if not obj.nurse:
            return None
        return getattr(obj.nurse, "first_name", None)

    def get_nurse_last_name(self, obj):
        if not obj.nurse:
            return None
        return getattr(obj.nurse, "last_name", None)

    # Provider fields (doctor)
    def get_provider_name(self, obj):
        return _get_user_name(obj.provider)

    def get_provider_first_name(self, obj):
        if not obj.provider:
            return None
        return getattr(obj.provider, "first_name", None)

    def get_provider_last_name(self, obj):
        if not obj.provider:
            return None
        return getattr(obj.provider, "last_name", None)

    # Created by fields
    def get_created_by_name(self, obj):
        return _get_user_name(obj.created_by)

    def get_created_by_first_name(self, obj):
        if not obj.created_by:
            return None
        return getattr(obj.created_by, "first_name", None)

    def get_created_by_last_name(self, obj):
        if not obj.created_by:
            return None
        return getattr(obj.created_by, "last_name", None)

    # Timeline event names
    def get_paused_by_name(self, obj):
        return _get_user_name(obj.paused_by)

    def get_resumed_by_name(self, obj):
        return _get_user_name(obj.resumed_by)

    def get_labs_skipped_by_name(self, obj):
        return _get_user_name(obj.labs_skipped_by)

    def get_clinical_finalized_by_name(self, obj):
        return _get_user_name(obj.clinical_finalized_by)

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
            "amendment_type",  # ✅ ADDED THIS FIELD
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
        m = self.context.get("attachments_map") or {}
        return m.get(obj.id, [])