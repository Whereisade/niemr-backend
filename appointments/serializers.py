from datetime import timedelta
from django.db.models import Q
from rest_framework import serializers

from .models import Appointment
from .enums import ApptStatus
from facilities.models import Facility


class AppointmentSerializer(serializers.ModelSerializer):
    """
    Full appointment serializer with display fields for patient, provider, facility,
    and linked encounter information.
    """
    # Display-only fields
    patient_name = serializers.SerializerMethodField(read_only=True)
    provider_name = serializers.SerializerMethodField(read_only=True)
    facility_name = serializers.SerializerMethodField(read_only=True)
    
    # Encounter linking info
    has_encounter = serializers.SerializerMethodField(read_only=True)
    encounter_status = serializers.SerializerMethodField(read_only=True)
    
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
        """Return 'First Last' for the patient if available."""
        if not obj.patient_id:
            return None
        first = getattr(obj.patient, "first_name", "") or ""
        last = getattr(obj.patient, "last_name", "") or ""
        name = (first + " " + last).strip()
        return name or str(obj.patient)

    def get_provider_name(self, obj):
        """Return 'First Last' for the provider if available."""
        if not obj.provider_id:
            return None
        first = getattr(obj.provider, "first_name", "") or ""
        last = getattr(obj.provider, "last_name", "") or ""
        name = (first + " " + last).strip()
        if name:
            return name
        return getattr(obj.provider, "email", None) or str(obj.provider)

    def get_facility_name(self, obj):
        """Return facility name for display."""
        if not obj.facility_id:
            return None
        return getattr(obj.facility, "name", None) or str(obj.facility)

    def get_has_encounter(self, obj):
        """Check if appointment has a linked encounter."""
        return obj.encounter_id is not None

    def get_encounter_status(self, obj):
        """
        Return the linked encounter's status if one exists.
        Uses prefetched data if available, otherwise does a lookup.
        """
        if not obj.encounter_id:
            return None
        
        # Try to use prefetched encounter if available
        encounter = getattr(obj, "_prefetched_encounter", None)
        if encounter:
            return encounter.status
        
        # Fallback: do a lookup (avoid N+1 by using select_related in queryset)
        try:
            from encounters.models import Encounter
            enc = Encounter.objects.filter(id=obj.encounter_id).values("status").first()
            return enc.get("status") if enc else None
        except Exception:
            return None

    def get_can_start_encounter(self, obj):
        """
        Determine if an encounter can be started for this appointment.
        
        Cannot start encounter if:
        - Appointment is CANCELLED, COMPLETED, or NO_SHOW
        - Appointment already has an active encounter (not CLOSED/CROSSED_OUT)
        """
        # Cannot start on terminal appointment statuses
        if obj.status in (ApptStatus.CANCELLED, ApptStatus.COMPLETED, ApptStatus.NO_SHOW):
            return False
        
        # If no encounter linked, can start
        if not obj.encounter_id:
            return True
        
        # If encounter exists, check its status
        enc_status = self.get_encounter_status(obj)
        if enc_status is None:
            return True  # Encounter was deleted or doesn't exist
        
        # Can only start new encounter if previous is closed/crossed out
        return enc_status in ("CLOSED", "CROSSED_OUT")

    def get_available_actions(self, obj):
        """
        Return list of available status transition actions based on current state.
        
        This centralizes the logic that was previously duplicated in frontend.
        """
        status = obj.status
        enc_status = self.get_encounter_status(obj)
        
        # Terminal states - no actions
        if status in (ApptStatus.COMPLETED, ApptStatus.CANCELLED, ApptStatus.NO_SHOW):
            return []
        
        actions = []
        
        if status == ApptStatus.SCHEDULED:
            # If encounter is active, only allow cancel (patient left without being seen)
            if enc_status and enc_status not in ("CLOSED", "CROSSED_OUT"):
                actions = ["cancel"]
            else:
                actions = ["check_in", "cancel", "no_show"]
        
        elif status == ApptStatus.CHECKED_IN:
            # Once checked in, can complete or cancel
            # If encounter is in progress, they should use encounter close
            if enc_status and enc_status not in ("CLOSED", "CROSSED_OUT"):
                actions = ["cancel"]  # Can still cancel if patient leaves
            else:
                actions = ["complete", "cancel"]
        
        return actions

    def validate(self, attrs):
        """
        Validate appointment data:
        - Infer facility from user/patient
        - Check for overlapping appointments
        - Validate time range
        """
        request = self.context["request"]
        user = request.user
        patient = attrs.get("patient")

        # 1) Start from the staff user's facility (most restrictive & safe)
        facility = getattr(user, "facility", None)

        # 2) If user has no facility (PATIENT or SUPER_ADMIN), try payload
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

        # 5) Final safety check
        if facility is None:
            raise serializers.ValidationError("Facility is required for appointments.")

        start_at = attrs.get("start_at")
        end_at = attrs.get("end_at")

        if not start_at or not end_at or end_at <= start_at:
            raise serializers.ValidationError("Invalid start/end times")

        # Store inferred facility for create()
        self._facility = facility

        # Prevent overlaps for the same provider in the same facility
        provider = attrs.get("provider")
        if provider:
            qs = Appointment.objects.filter(
                facility=facility,
                provider=provider,
                status__in=[ApptStatus.SCHEDULED, ApptStatus.CHECKED_IN],
            )
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
    """
    Serializer for updating appointments.
    Prevents changing facility after creation.
    """
    class Meta(AppointmentSerializer.Meta):
        read_only_fields = [
            "facility",
            "created_by",
            "created_at",
            "updated_at",
        ]


class AppointmentListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for list views with essential display fields.
    """
    patient_name = serializers.SerializerMethodField(read_only=True)
    provider_name = serializers.SerializerMethodField(read_only=True)
    facility_name = serializers.SerializerMethodField(read_only=True)
    has_encounter = serializers.SerializerMethodField(read_only=True)
    encounter_status = serializers.SerializerMethodField(read_only=True)
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
            "appt_type",
            "status",
            "reason",
            "start_at",
            "end_at",
            "encounter_id",
            "has_encounter",
            "encounter_status",
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

    def get_provider_name(self, obj):
        if not obj.provider_id:
            return None
        first = getattr(obj.provider, "first_name", "") or ""
        last = getattr(obj.provider, "last_name", "") or ""
        name = (first + " " + last).strip()
        if name:
            return name
        return getattr(obj.provider, "email", None) or str(obj.provider)

    def get_facility_name(self, obj):
        if not obj.facility_id:
            return None
        return getattr(obj.facility, "name", None) or str(obj.facility)

    def get_has_encounter(self, obj):
        return obj.encounter_id is not None

    def get_encounter_status(self, obj):
        if not obj.encounter_id:
            return None
        try:
            from encounters.models import Encounter
            enc = Encounter.objects.filter(id=obj.encounter_id).values("status").first()
            return enc.get("status") if enc else None
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
        
        actions = []
        
        if status == ApptStatus.SCHEDULED:
            if enc_status and enc_status not in ("CLOSED", "CROSSED_OUT"):
                actions = ["cancel"]
            else:
                actions = ["check_in", "cancel", "no_show"]
        elif status == ApptStatus.CHECKED_IN:
            if enc_status and enc_status not in ("CLOSED", "CROSSED_OUT"):
                actions = ["cancel"]
            else:
                actions = ["complete", "cancel"]
        
        return actions
