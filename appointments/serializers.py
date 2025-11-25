from datetime import timedelta
from django.db.models import Q
from rest_framework import serializers
from .models import Appointment
from .enums import ApptStatus


class AppointmentSerializer(serializers.ModelSerializer):
    # 👇 New display-only fields
    patient_name = serializers.SerializerMethodField(read_only=True)
    provider_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Appointment
        fields = [
            "id",
            "patient",
            "patient_name",   
            "facility",
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
            "lab_order_id",
            "imaging_request_id",
            "notify_email",
            "last_notified_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "facility",
            "created_by",
            "status",
            "last_notified_at",
            "created_at",
            "updated_at",
        ]

    # 👇 These MUST be indented inside the class (4 spaces), not at column 0
    def get_patient_name(self, obj):
        """
        Return 'First Last' for the patient if available,
        otherwise fall back to Patient.__str__().
        """
        if not obj.patient_id:
            return None
        first = getattr(obj.patient, "first_name", "") or ""
        last = getattr(obj.patient, "last_name", "") or ""
        name = (first + " " + last).strip()
        return name or str(obj.patient)

    def get_provider_name(self, obj):
        """
        Return 'First Last' for the provider if available,
        otherwise fall back to email or Provider.__str__().
        """
        if not obj.provider_id:
            return None
        first = getattr(obj.provider, "first_name", "") or ""
        last = getattr(obj.provider, "last_name", "") or ""
        name = (first + " " + last).strip()
        if name:
            return name
        return getattr(obj.provider, "email", None) or str(obj.provider)

    def validate(self, attrs):
        # infer facility from request user or patient
        request = self.context["request"]
        user = request.user
        patient = attrs.get("patient")
        facility = user.facility or getattr(patient, "facility", None)
        start_at = attrs.get("start_at")
        end_at = attrs.get("end_at")

        if not start_at or not end_at or end_at <= start_at:
            raise serializers.ValidationError("Invalid start/end times")

        # store inferred facility for create()
        self._facility = facility

        # prevent overlaps for the same provider in the same facility
        provider = attrs.get("provider")
        if provider:
            qs = Appointment.objects.filter(
                facility=facility,
                provider=provider,
                status__in=[ApptStatus.SCHEDULED, ApptStatus.CHECKED_IN],
            )
            if self.instance:
                qs = qs.exclude(id=self.instance.id)
            # overlap if (start < other.end) and (end > other.start)
            if qs.filter(start_at__lt=end_at, end_at__gt=start_at).exists():
                raise serializers.ValidationError(
                    "Provider already has an overlapping appointment in this time range."
                )

        return attrs

    def create(self, validated):
        user = self.context["request"].user
        appt = Appointment.objects.create(
            facility=self._facility,
            created_by=user,
            status=ApptStatus.SCHEDULED,
            **validated,
        )
        return appt


class AppointmentUpdateSerializer(AppointmentSerializer):
    class Meta(AppointmentSerializer.Meta):
        read_only_fields = ["facility", "created_by", "created_at", "updated_at"]
