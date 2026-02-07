from django.db.models import Q
from rest_framework import serializers

from .models import Appointment
from .enums import ApptStatus
from facilities.models import Facility


def _get_user_name(user):
    """Best-effort full name for display."""
    if not user:
        return None
    first = getattr(user, "first_name", "") or ""
    last = getattr(user, "last_name", "") or ""
    full = f"{first} {last}".strip()
    return full if full else (getattr(user, "email", None) or str(user))


def _get_prefetched_encounter(obj):
    """Use encounter object attached by the viewset to avoid N+1 lookups."""
    return getattr(obj, "_prefetched_encounter", None)


class AppointmentSerializer(serializers.ModelSerializer):
    """Full appointment serializer with display fields + linked encounter info."""

    # Display-only fields
    patient_name = serializers.SerializerMethodField(read_only=True)
    provider_name = serializers.SerializerMethodField(read_only=True)
    facility_name = serializers.SerializerMethodField(read_only=True)

    # NEW: nurse display (derived from linked encounter)
    nurse = serializers.SerializerMethodField(read_only=True)
    nurse_name = serializers.SerializerMethodField(read_only=True)

    # Encounter linking info
    has_encounter = serializers.SerializerMethodField(read_only=True)
    encounter_status = serializers.SerializerMethodField(read_only=True)
    encounter_stage = serializers.SerializerMethodField(read_only=True)

    # Computed fields for UI
    can_start_encounter = serializers.SerializerMethodField(read_only=True)
    available_actions = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Appointment
        fields = [
            "id",
            "patient",
            "patient_name",
            "facility",
            "facility_name",
            "provider",
            "provider_name",
            "nurse",
            "nurse_name",
            "created_by",
            "appt_type",
            "status",
            "reason",
            "notes",
            "start_at",
            "end_at",
            "encounter_id",
            "has_encounter",
            "encounter_status",
            "encounter_stage",
            "can_start_encounter",
            "available_actions",
            "lab_order_id",
            "imaging_request_id",
            "notify_email",
            "last_notified_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "created_by",
            "last_notified_at",
            "created_at",
            "updated_at",
        ]

    def get_patient_name(self, obj):
        if not obj.patient_id:
            return None
        first = getattr(obj.patient, "first_name", "") or ""
        last = getattr(obj.patient, "last_name", "") or ""
        name = (first + " " + last).strip()
        return name or str(obj.patient)

    def get_facility_name(self, obj):
        if not obj.facility_id:
            return None
        return getattr(obj.facility, "name", None) or str(obj.facility)

    def get_provider_name(self, obj):
        """
        Provider column should reflect the doctor assigned to the linked encounter
        (when present), otherwise fall back to appointment.provider.
        """
        enc = _get_prefetched_encounter(obj)
        if enc and getattr(enc, "provider", None):
            return _get_user_name(enc.provider)

        # Fallback to appointment.provider
        if not obj.provider_id:
            return None
        return _get_user_name(obj.provider)

    def get_nurse(self, obj):
        enc = _get_prefetched_encounter(obj)
        return getattr(enc, "nurse_id", None) if enc else None

    def get_nurse_name(self, obj):
        enc = _get_prefetched_encounter(obj)
        if enc and getattr(enc, "nurse", None):
            return _get_user_name(enc.nurse)

        # Fallback (no prefetched encounter): quick lookup by encounter_id
        if not obj.encounter_id:
            return None
        try:
            from encounters.models import Encounter

            enc2 = (
                Encounter.objects.filter(id=obj.encounter_id)
                .select_related("nurse")
                .only("id", "nurse_id", "nurse__first_name", "nurse__last_name", "nurse__email")
                .first()
            )
            return _get_user_name(enc2.nurse) if enc2 and enc2.nurse_id else None
        except Exception:
            return None

    def get_has_encounter(self, obj):
        return obj.encounter_id is not None

    def get_encounter_status(self, obj):
        if not obj.encounter_id:
            return None

        enc = _get_prefetched_encounter(obj)
        if enc is not None:
            return getattr(enc, "status", None)

        try:
            from encounters.models import Encounter

            row = Encounter.objects.filter(id=obj.encounter_id).values("status").first()
            return row.get("status") if row else None
        except Exception:
            return None

    def get_encounter_stage(self, obj):
        if not obj.encounter_id:
            return None

        enc = _get_prefetched_encounter(obj)
        if enc is not None:
            return getattr(enc, "stage", None)

        try:
            from encounters.models import Encounter

            row = Encounter.objects.filter(id=obj.encounter_id).values("stage").first()
            return row.get("stage") if row else None
        except Exception:
            return None

    def get_can_start_encounter(self, obj):
        if obj.status in (ApptStatus.CANCELLED, ApptStatus.COMPLETED, ApptStatus.NO_SHOW):
            return False

        if not obj.encounter_id:
            return True

        enc_status = self.get_encounter_status(obj)
        if enc_status is None:
            return True

        return enc_status in ("CLOSED", "CROSSED_OUT")

    def get_available_actions(self, obj):
        status = obj.status
        enc_status = self.get_encounter_status(obj)

        if status in (ApptStatus.COMPLETED, ApptStatus.CANCELLED, ApptStatus.NO_SHOW):
            return []

        if status == ApptStatus.SCHEDULED:
            if enc_status and enc_status not in ("CLOSED", "CROSSED_OUT"):
                return ["cancel"]
            return ["check_in", "cancel", "no_show"]

        if status == ApptStatus.CHECKED_IN:
            if enc_status and enc_status not in ("CLOSED", "CROSSED_OUT"):
                return ["cancel"]
            return ["complete", "cancel"]

        return []

    def validate(self, attrs):
        """
        Validate appointment data:
        - Infer facility from user/patient (optional for independent providers)
        - Check for overlapping appointments
        - Validate time range
        """
        request = self.context["request"]
        user = request.user
        patient = attrs.get("patient")

        # 1) Start from the staff user's facility (most restrictive & safe)
        facility = getattr(user, "facility", None)

        # 2) If user has no facility (PATIENT, SUPER_ADMIN, or independent provider), try payload
        if facility is None:
            facility_val = attrs.get("facility", None)

            if isinstance(facility_val, Facility):
                facility = facility_val
            else:
                facility_id = facility_val or self.initial_data.get("facility")
                if facility_id not in (None, "", "null"):
                    try:
                        facility = Facility.objects.get(id=facility_id)
                    except (ValueError, TypeError, Facility.DoesNotExist):
                        raise serializers.ValidationError("Invalid facility id.")

        # 3) Fallback to the patient's facility if still None
        if facility is None and patient is not None:
            facility = getattr(patient, "facility", None)

        # 4) For updates, fall back to the existing instance's facility
        if facility is None and getattr(self, "instance", None) is not None:
            facility = getattr(self.instance, "facility", None)

        # 5) ✅ FIX: Only require facility if user has facility_id (facility-based staff)
        user_has_facility = getattr(user, "facility_id", None) is not None
        if facility is None and user_has_facility:
            raise serializers.ValidationError(
                "Facility is required for facility-based appointments."
            )

        start_at = attrs.get("start_at")
        end_at = attrs.get("end_at")

        if not start_at or not end_at or end_at <= start_at:
            raise serializers.ValidationError("Invalid start/end times")

        # Store inferred facility for create()
        self._facility = facility

        # ✅ FIX: Prevent overlaps for the same provider (facility-scoped or global)
        provider = attrs.get("provider")
        if provider:
            # Base query: filter by provider and active statuses
            qs = Appointment.objects.filter(
                provider=provider,
                status__in=[ApptStatus.SCHEDULED, ApptStatus.CHECKED_IN],
            )
            
            # Only filter by facility if one exists (facility-based vs independent)
            if facility:
                qs = qs.filter(facility=facility)
            
            if self.instance:
                qs = qs.exclude(id=self.instance.id)
            
            # Overlap if (start < other.end) and (end > other.start)
            if qs.filter(start_at__lt=end_at, end_at__gt=start_at).exists():
                raise serializers.ValidationError(
                    "Provider already has an overlapping appointment in this time range."
                )

        return attrs

    def create(self, validated_data):
        user = self.context["request"].user

        # Avoid passing 'facility' twice
        validated_data.pop("facility", None)

        appt = Appointment.objects.create(
            facility=self._facility,
            created_by=user,
            status=ApptStatus.SCHEDULED,
            **validated_data,
        )
        return appt


class AppointmentUpdateSerializer(AppointmentSerializer):
    """Serializer for updating appointments. Prevents changing facility after creation."""

    class Meta(AppointmentSerializer.Meta):
        read_only_fields = [
            "facility",
            "created_by",
            "created_at",
            "updated_at",
        ]


class AppointmentListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views with essential display fields."""

    patient_name = serializers.SerializerMethodField(read_only=True)
    provider_name = serializers.SerializerMethodField(read_only=True)
    facility_name = serializers.SerializerMethodField(read_only=True)
    # ✅ Show the facility name for facility bookings, and business/practice name for independent providers
    provider_org_name = serializers.SerializerMethodField(read_only=True)

    # NEW: nurse display (derived from linked encounter)
    nurse = serializers.SerializerMethodField(read_only=True)
    nurse_name = serializers.SerializerMethodField(read_only=True)

    has_encounter = serializers.SerializerMethodField(read_only=True)
    encounter_status = serializers.SerializerMethodField(read_only=True)
    encounter_stage = serializers.SerializerMethodField(read_only=True)
    can_start_encounter = serializers.SerializerMethodField(read_only=True)
    available_actions = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Appointment
        fields = [
            "id",
            "patient",
            "patient_name",
            "facility",
            "facility_name",
            "provider_org_name",
            "provider",
            "provider_name",
            "nurse",
            "nurse_name",
            "appt_type",
            "status",
            "reason",
            "start_at",
            "end_at",
            "encounter_id",
            "has_encounter",
            "encounter_status",
            "encounter_stage",
            "can_start_encounter",
            "available_actions",
            "created_at",
        ]

    def get_patient_name(self, obj):
        if not obj.patient_id:
            return None
        first = getattr(obj.patient, "first_name", "") or ""
        last = getattr(obj.patient, "last_name", "") or ""
        name = (first + " " + last).strip()
        return name or str(obj.patient)

    def get_facility_name(self, obj):
        if not obj.facility_id:
            return None
        return getattr(obj.facility, "name", None) or str(obj.facility)

    def get_provider_org_name(self, obj):
        """
        For patient list UX, we want the "Facility" column to still show something
        even when the booking is with an independent provider (lab/pharmacy/doctor).

        - Facility booking: facility.name
        - Independent provider booking: provider_profile.business_name (fallback to provider full name)
        """
        # Facility-based appointment
        if obj.facility_id:
            return getattr(obj.facility, "name", None) or str(obj.facility)

        # Independent provider appointment
        provider = getattr(obj, "provider", None)
        if not provider:
            return None

        prof = getattr(provider, "provider_profile", None)
        if prof is not None:
            try:
                name = (prof.get_display_name() or "").strip()
            except Exception:
                name = (getattr(prof, "business_name", "") or "").strip()
            if name:
                return name

        # Fallback to provider name
        first = getattr(provider, "first_name", "") or ""
        last = getattr(provider, "last_name", "") or ""
        full = (first + " " + last).strip()
        if full:
            return full
        return getattr(provider, "email", None) or None

    def get_provider_name(self, obj):
        enc = _get_prefetched_encounter(obj)
        if enc and getattr(enc, "provider", None):
            return _get_user_name(enc.provider)

        if not obj.provider_id:
            return None
        return _get_user_name(obj.provider)

    def get_nurse(self, obj):
        enc = _get_prefetched_encounter(obj)
        return getattr(enc, "nurse_id", None) if enc else None

    def get_nurse_name(self, obj):
        enc = _get_prefetched_encounter(obj)
        if enc and getattr(enc, "nurse", None):
            return _get_user_name(enc.nurse)

        if not obj.encounter_id:
            return None
        try:
            from encounters.models import Encounter

            enc2 = (
                Encounter.objects.filter(id=obj.encounter_id)
                .select_related("nurse")
                .only("id", "nurse_id", "nurse__first_name", "nurse__last_name", "nurse__email")
                .first()
            )
            return _get_user_name(enc2.nurse) if enc2 and enc2.nurse_id else None
        except Exception:
            return None

    def get_has_encounter(self, obj):
        return obj.encounter_id is not None

    def get_encounter_status(self, obj):
        if not obj.encounter_id:
            return None

        enc = _get_prefetched_encounter(obj)
        if enc is not None:
            return getattr(enc, "status", None)

        try:
            from encounters.models import Encounter

            row = Encounter.objects.filter(id=obj.encounter_id).values("status").first()
            return row.get("status") if row else None
        except Exception:
            return None

    def get_encounter_stage(self, obj):
        if not obj.encounter_id:
            return None

        enc = _get_prefetched_encounter(obj)
        if enc is not None:
            return getattr(enc, "stage", None)

        try:
            from encounters.models import Encounter

            row = Encounter.objects.filter(id=obj.encounter_id).values("stage").first()
            return row.get("stage") if row else None
        except Exception:
            return None

    def get_can_start_encounter(self, obj):
        if obj.status in (ApptStatus.CANCELLED, ApptStatus.COMPLETED, ApptStatus.NO_SHOW):
            return False
        if not obj.encounter_id:
            return True
        enc_status = self.get_encounter_status(obj)
        if enc_status is None:
            return True
        return enc_status in ("CLOSED", "CROSSED_OUT")

    def get_available_actions(self, obj):
        status = obj.status
        enc_status = self.get_encounter_status(obj)

        if status in (ApptStatus.COMPLETED, ApptStatus.CANCELLED, ApptStatus.NO_SHOW):
            return []

        if status == ApptStatus.SCHEDULED:
            if enc_status and enc_status not in ("CLOSED", "CROSSED_OUT"):
                return ["cancel"]
            return ["check_in", "cancel", "no_show"]

        if status == ApptStatus.CHECKED_IN:
            if enc_status and enc_status not in ("CLOSED", "CROSSED_OUT"):
                return ["cancel"]
            return ["complete", "cancel"]

        return []