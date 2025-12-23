from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.db.models import Q, Case, When, IntegerField, Value, Subquery
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.contrib.auth import get_user_model

from .models import Appointment
from .serializers import (
    AppointmentSerializer,
    AppointmentUpdateSerializer,
    AppointmentListSerializer,
)
from .permissions import IsStaff, CanViewAppointment
from .enums import ApptStatus
from .services.notify import send_confirmation, send_reminder
from notifications.services.notify import notify_user, notify_users, notify_facility_roles
from notifications.enums import Topic, Priority
from accounts.enums import UserRole

User = get_user_model()


class AppointmentViewSet(
    viewsets.GenericViewSet,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
):
    queryset = Appointment.objects.select_related(
        "patient", "facility", "provider", "created_by"
    )
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list":
            return AppointmentListSerializer
        if self.action in ("update", "partial_update"):
            return AppointmentUpdateSerializer
        return AppointmentSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user

        # Role-based filtering
        if u.role == "PATIENT":
            base_patient = getattr(u, "patient_profile", None)
            if base_patient:
                # Patient can see own appointments and dependents'
                q = q.filter(
                    Q(patient=base_patient) | Q(patient__parent_patient=base_patient)
                )
            else:
                q = q.none()
        elif u.facility_id:
            # Facility-scoped users
            q = q.filter(facility_id=u.facility_id)

            # IMPORTANT: Facility doctors should only see appointments assigned to them.
            # Other facility roles (SUPER_ADMIN, ADMIN, NURSE, FRONTDESK, etc.) can see all.
            if u.role == "DOCTOR":
                # Primary: appointment.provider points to the assigned doctor
                # Fallback: if older data only has encounter.provider, include those too.
                try:
                    from encounters.models import Encounter

                    enc_ids = Encounter.objects.filter(provider_id=u.id).values("id")
                    q = q.filter(Q(provider_id=u.id) | Q(encounter_id__in=Subquery(enc_ids)))
                except Exception:
                    q = q.filter(provider_id=u.id)

            # Optional: facility staff can still request only their own appointments via ?mine=true
            # (ignored for doctors because they are always scoped to their own).
            elif self.request.query_params.get("mine") in ("true", "True", "1"):
                q = q.filter(provider_id=u.id)
        else:
            # Independent provider without facility - only own appointments
            q = q.filter(provider_id=u.id)

        # Query params filtering
        patient_id = self.request.query_params.get("patient")
        provider_id = self.request.query_params.get("provider")
        status_ = self.request.query_params.get("status")
        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        s = self.request.query_params.get("s") or self.request.query_params.get("q")
        date_filter = self.request.query_params.get("date")

        if patient_id:
            q = q.filter(patient_id=patient_id)
        if provider_id:
            q = q.filter(provider_id=provider_id)
        if status_:
            # Handle both upper and lower case status values
            q = q.filter(status__iexact=status_)
        if start:
            parsed_start = parse_datetime(start)
            q = q.filter(start_at__gte=parsed_start or start)
        if end:
            parsed_end = parse_datetime(end)
            q = q.filter(end_at__lte=parsed_end or end)
        if s:
            q = q.filter(
                Q(reason__icontains=s)
                | Q(notes__icontains=s)
                | Q(patient__first_name__icontains=s)
                | Q(patient__last_name__icontains=s)
            )

        # Date presets for convenience
        if date_filter:
            today = timezone.now().date()
            if date_filter == "today":
                q = q.filter(start_at__date=today)
            elif date_filter == "tomorrow":
                q = q.filter(start_at__date=today + timezone.timedelta(days=1))
            elif date_filter == "this_week":
                week_start = today - timezone.timedelta(days=today.weekday())
                week_end = week_start + timezone.timedelta(days=6)
                q = q.filter(start_at__date__gte=week_start, start_at__date__lte=week_end)
            elif date_filter == "next_7d":
                q = q.filter(
                    start_at__date__gte=today,
                    start_at__date__lte=today + timezone.timedelta(days=7),
                )
            # 'all' or unknown values = no date filter

        # Order list with active (new/current) appointments first
        status_rank = Case(
            When(status__iexact=ApptStatus.CHECKED_IN, then=Value(0)),
            When(status__iexact=ApptStatus.SCHEDULED, then=Value(1)),
            When(status__iexact=ApptStatus.COMPLETED, then=Value(2)),
            When(status__iexact=ApptStatus.CANCELLED, then=Value(3)),
            When(status__iexact=ApptStatus.NO_SHOW, then=Value(4)),
            default=Value(99),
            output_field=IntegerField(),
        )

        return q.annotate(_status_rank=status_rank).order_by("_status_rank", "start_at", "id")


    def _attach_prefetched_encounters(self, appts):
        """Attach Encounter objects to each appointment (as _prefetched_encounter) to avoid N+1."""
        try:
            from encounters.models import Encounter
        except Exception:
            return

        encounter_ids = [a.encounter_id for a in appts if getattr(a, "encounter_id", None)]
        if not encounter_ids:
            return

        enc_map = {
            e.id: e
            for e in Encounter.objects.filter(id__in=encounter_ids).select_related("nurse", "provider")
        }

        for a in appts:
            if getattr(a, "encounter_id", None):
                a._prefetched_encounter = enc_map.get(a.encounter_id)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        if page is not None:
            # page is already a list
            self._attach_prefetched_encounters(page)
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        objs = list(queryset)
        self._attach_prefetched_encounters(objs)
        serializer = self.get_serializer(objs, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        """
        Create appointment with proper patient/dependent handling.
        """
        user = request.user
        data = request.data.copy()

        if user.role == "PATIENT":
            base_patient = getattr(user, "patient_profile", None)
            if not base_patient:
                return Response(
                    {"detail": "No patient profile linked to this user."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            raw_patient_id = data.get("patient")
            target_patient_id = None
            if raw_patient_id not in (None, "", "null"):
                try:
                    target_patient_id = int(raw_patient_id)
                except (TypeError, ValueError):
                    return Response(
                        {"detail": "Invalid patient id."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            if target_patient_id is None or target_patient_id == base_patient.id:
                data["patient"] = base_patient.id
            else:
                allowed_ids = set(base_patient.dependents.values_list("id", flat=True))
                if target_patient_id not in allowed_ids:
                    return Response(
                        {
                            "detail": (
                                "You can only book appointments for yourself "
                                "or your registered dependents."
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                data["patient"] = target_patient_id
        else:
            self.permission_classes = [IsAuthenticated, IsStaff]
            self.check_permissions(request)

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        resp = Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
            headers=self.get_success_headers(serializer.data),
        )

        # Auto-link patient to facility
        try:
            appt = Appointment.objects.select_related("patient", "facility").get(
                id=resp.data["id"]
            )
            patient = appt.patient
            if appt.facility_id and (
                not patient.facility_id or patient.facility_id != appt.facility_id
            ):
                patient.facility = appt.facility
                patient.save(update_fields=["facility"])
        except Exception:
            pass

        # Send confirmation email
        try:
            appt = Appointment.objects.get(id=resp.data["id"])
            send_confirmation(appt)
        except Exception:
            pass

        # In-app notifications (facility staff + provider + patient)
        try:
            appt = Appointment.objects.select_related(
                "patient", "facility", "provider", "patient__user"
            ).get(id=resp.data["id"])

            facility_id = appt.facility_id
            patient_name = getattr(appt.patient, "full_name", None) or f"Patient #{appt.patient_id}"
            when = appt.start_at.strftime("%Y-%m-%d %H:%M") if appt.start_at else ""

            title = "New appointment scheduled"
            body = f"{patient_name} • {when}\nReason: {appt.reason or '-'}"
            data_payload = {"appointment_id": appt.id, "patient_id": appt.patient_id}
            group_key = f"APPT:{appt.id}:CREATED"

            # Provider
            if appt.provider_id:
                notify_user(
                    user=appt.provider,
                    topic=Topic.APPOINTMENT_CONFIRMED,
                    priority=Priority.NORMAL,
                    title=title,
                    body=body,
                    facility_id=facility_id,
                    data=data_payload,
                    action_url="/facility/appointments",
                    group_key=group_key,
                )

            # Facility staff
            if facility_id:
                staff_roles = [
                    UserRole.SUPER_ADMIN,
                    UserRole.ADMIN,
                    UserRole.FRONTDESK,
                    UserRole.NURSE,
                ]
                staff_users = (
                    User.objects.filter(facility_id=facility_id, role__in=staff_roles)
                    .exclude(id=appt.provider_id)
                    .distinct()
                )
                notify_users(
                    users=staff_users,
                    topic=Topic.APPOINTMENT_CONFIRMED,
                    priority=Priority.NORMAL,
                    title=title,
                    body=body,
                    facility_id=facility_id,
                    data=data_payload,
                    action_url="/facility/appointments",
                    group_key=group_key,
                )

            # Patient
            if getattr(appt.patient, "user_id", None):
                notify_user(
                    user=appt.patient.user,
                    topic=Topic.APPOINTMENT_CONFIRMED,
                    priority=Priority.LOW,
                    title="Appointment booked",
                    body=f"Your appointment is scheduled for {when}.",
                    facility_id=facility_id,
                    data=data_payload,
                    action_url="/patient/appointments",
                    group_key=group_key,
                )
        except Exception:
            pass

        return resp

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewAppointment]
        self.check_object_permissions(request, obj)
        return Response(AppointmentSerializer(obj, context={"request": request}).data)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        if request.user.role != "PATIENT":
            self.permission_classes = [IsAuthenticated, IsStaff]
            self.check_permissions(request)
        return super().update(request, *args, **kwargs)

    # ─────────────────────────────────────────────────────────────
    # Status transition actions
    # ─────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def check_in(self, request, pk=None):
        """Mark appointment as checked-in (patient has arrived)."""
        appt = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        if appt.status != ApptStatus.SCHEDULED:
            return Response(
                {"detail": "Only scheduled appointments can be checked-in."},
                status=400,
            )

        appt.status = ApptStatus.CHECKED_IN
        appt.save(update_fields=["status", "updated_at"])

        try:
            when = appt.start_at.strftime("%Y-%m-%d %H:%M") if appt.start_at else ""
            payload = {"appointment_id": appt.id, "patient_id": appt.patient_id}
            patient_name = " ".join([p for p in [getattr(appt.patient, 'first_name', ''), getattr(appt.patient, 'middle_name', ''), getattr(appt.patient, 'last_name', '')] if p]).strip()
            group_key = f"APPT:{appt.id}:CHECKED_IN"
            if appt.provider_id:
                notify_user(
                    user=appt.provider,
                    topic=Topic.APPOINTMENT_CHECKED_IN,
                    priority=Priority.HIGH,
                    title="Patient checked-in",
                    body=f"Appointment {when} has been checked-in.",
                    facility_id=appt.facility_id,
                    data=payload,
                    action_url="/facility/appointments",
                    group_key=group_key,
                )
            if appt.patient and appt.patient.user_id:
                notify_user(
                    user=appt.patient.user,
                    topic=Topic.APPOINTMENT_CHECKED_IN,
                    priority=Priority.LOW,
                    title="Checked-in",
                    body=f"You have been checked-in for your appointment ({when}).",
                    facility_id=appt.facility_id,
                    data=payload,
                    action_url="/patient/appointments",
                    group_key=group_key,
                )
            # Ops feed (role-scoped): Nurses get check-in events
            if appt.facility_id:
                notify_facility_roles(
                    facility_id=appt.facility_id,
                    roles=[UserRole.NURSE],
                    topic=Topic.APPOINTMENT_CHECKED_IN,
                    priority=Priority.NORMAL,
                    title="Patient checked-in",
                    body=f"{patient_name} checked-in for {when}.",
                    data=payload,
                    action_url=f"/facility/appointments/{appt.id}",
                    group_key=group_key,
                )

        except Exception:
            pass

        return Response(
            AppointmentSerializer(appt, context={"request": request}).data
        )

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """
        Mark appointment as completed.
        If there's a linked encounter that's still open, this also closes it.
        """
        appt = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        if appt.status not in (ApptStatus.SCHEDULED, ApptStatus.CHECKED_IN):
            return Response(
                {"detail": "Only scheduled/checked-in appointments can be completed."},
                status=400,
            )

        appt.status = ApptStatus.COMPLETED
        appt.save(update_fields=["status", "updated_at"])

        try:
            when = appt.start_at.strftime("%Y-%m-%d %H:%M") if appt.start_at else ""
            payload = {"appointment_id": appt.id, "patient_id": appt.patient_id}
            group_key = f"APPT:{appt.id}:COMPLETED"
            if appt.provider_id:
                notify_user(
                    user=appt.provider,
                    topic=Topic.APPOINTMENT_COMPLETED,
                    priority=Priority.NORMAL,
                    title="Appointment completed",
                    body=f"Appointment {when} has been marked completed.",
                    facility_id=appt.facility_id,
                    data=payload,
                    action_url="/facility/appointments",
                    group_key=group_key,
                )
            if appt.patient and appt.patient.user_id:
                notify_user(
                    user=appt.patient.user,
                    topic=Topic.APPOINTMENT_COMPLETED,
                    priority=Priority.LOW,
                    title="Appointment completed",
                    body="Your appointment has been completed.",
                    facility_id=appt.facility_id,
                    data=payload,
                    action_url="/patient/appointments",
                    group_key=group_key,
                )
        except Exception:
            pass

        # Also close linked encounter if it's still open
        if appt.encounter_id:
            try:
                from encounters.models import Encounter
                from encounters.enums import EncounterStatus

                enc = Encounter.objects.filter(id=appt.encounter_id).first()
                if enc and enc.status not in (
                    EncounterStatus.CLOSED,
                    EncounterStatus.CROSSED_OUT,
                ):
                    enc.status = EncounterStatus.CLOSED
                    enc.save(update_fields=["status", "updated_at"])
            except Exception:
                pass

        return Response(
            AppointmentSerializer(appt, context={"request": request}).data
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """
        Cancel appointment.
        Patients can cancel their own; staff can cancel any in their facility.
        """
        appt = self.get_object()

        # Permission check
        if request.user.role != "PATIENT":
            self.permission_classes = [IsAuthenticated, IsStaff]
            self.check_permissions(request)
        else:
            # Patients can only cancel their own
            base_patient = getattr(request.user, "patient_profile", None)
            if not base_patient:
                return Response(
                    {"detail": "No patient profile linked."}, status=403
                )
            allowed_ids = {base_patient.id} | set(
                base_patient.dependents.values_list("id", flat=True)
            )
            if appt.patient_id not in allowed_ids:
                return Response(
                    {"detail": "You can only cancel your own appointments."},
                    status=403,
                )

        if appt.status == ApptStatus.COMPLETED:
            return Response(
                {"detail": "Completed appointments cannot be cancelled."},
                status=400,
            )

        appt.status = ApptStatus.CANCELLED
        appt.save(update_fields=["status", "updated_at"])

        try:
            when = appt.start_at.strftime("%Y-%m-%d %H:%M") if appt.start_at else ""
            payload = {"appointment_id": appt.id, "patient_id": appt.patient_id}
            patient_name = " ".join([p for p in [getattr(appt.patient, 'first_name', ''), getattr(appt.patient, 'middle_name', ''), getattr(appt.patient, 'last_name', '')] if p]).strip()
            group_key = f"APPT:{appt.id}:CANCELLED"

            if appt.provider_id:
                notify_user(
                    user=appt.provider,
                    topic=Topic.APPOINTMENT_CANCELLED,
                    priority=Priority.NORMAL,
                    title="Appointment cancelled",
                    body=f"An appointment ({when}) was cancelled.",
                    facility_id=appt.facility_id,
                    data=payload,
                    action_url="/facility/appointments",
                    group_key=group_key,
                )

            if appt.patient and appt.patient.user_id:
                notify_user(
                    user=appt.patient.user,
                    topic=Topic.APPOINTMENT_CANCELLED,
                    priority=Priority.LOW,
                    title="Appointment cancelled",
                    body="Your appointment was cancelled.",
                    facility_id=appt.facility_id,
                    data=payload,
                    action_url="/patient/appointments",
                    group_key=group_key,
                )
            # Ops feed (role-scoped): Frontdesk gets cancellation events
            if appt.facility_id:
                cancelled_by = "patient" if request.user.role == "PATIENT" else "staff"
                notify_facility_roles(
                    facility_id=appt.facility_id,
                    roles=[UserRole.FRONTDESK],
                    topic=Topic.APPOINTMENT_CANCELLED,
                    priority=Priority.NORMAL,
                    title="Appointment cancelled",
                    body=f"{patient_name}'s appointment ({when}) was cancelled by {cancelled_by}.",
                    data=payload,
                    action_url=f"/facility/appointments/{appt.id}",
                    group_key=group_key,
                )

        except Exception:
            pass

        return Response(
            AppointmentSerializer(appt, context={"request": request}).data
        )

    @action(detail=True, methods=["post"])
    def no_show(self, request, pk=None):
        """Mark appointment as no-show (patient didn't arrive)."""
        appt = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        if appt.status != ApptStatus.SCHEDULED:
            return Response(
                {"detail": "Only scheduled appointments can be marked no-show."},
                status=400,
            )

        appt.status = ApptStatus.NO_SHOW
        appt.save(update_fields=["status", "updated_at"])

        try:
            when = appt.start_at.strftime("%Y-%m-%d %H:%M") if appt.start_at else ""
            payload = {"appointment_id": appt.id, "patient_id": appt.patient_id}
            group_key = f"APPT:{appt.id}:NO_SHOW"
            if appt.provider_id:
                notify_user(
                    user=appt.provider,
                    topic=Topic.APPOINTMENT_NO_SHOW,
                    priority=Priority.NORMAL,
                    title="Appointment no-show",
                    body=f"Patient did not show for {when}.",
                    facility_id=appt.facility_id,
                    data=payload,
                    action_url="/facility/appointments",
                    group_key=group_key,
                )
        except Exception:
            pass

        return Response(
            AppointmentSerializer(appt, context={"request": request}).data
        )

    # ─────────────────────────────────────────────────────────────
    # Utility endpoints
    # ─────────────────────────────────────────────────────────────

    @action(detail=False, methods=["get"])
    def statuses(self, request):
        """Return available appointment statuses."""
        return Response([{"value": c, "label": l} for c, l in ApptStatus.choices])

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """
        Return appointment counts by status for the current user's scope.
        Useful for dashboard widgets.
        """
        qs = self.get_queryset()
        
        # Optional date range
        date_filter = request.query_params.get("date", "today")
        today = timezone.now().date()
        
        if date_filter == "today":
            qs = qs.filter(start_at__date=today)
        elif date_filter == "this_week":
            week_start = today - timezone.timedelta(days=today.weekday())
            week_end = week_start + timezone.timedelta(days=6)
            qs = qs.filter(start_at__date__gte=week_start, start_at__date__lte=week_end)
        
        counts = {
            "total": qs.count(),
            "scheduled": qs.filter(status=ApptStatus.SCHEDULED).count(),
            "checked_in": qs.filter(status=ApptStatus.CHECKED_IN).count(),
            "completed": qs.filter(status=ApptStatus.COMPLETED).count(),
            "cancelled": qs.filter(status=ApptStatus.CANCELLED).count(),
            "no_show": qs.filter(status=ApptStatus.NO_SHOW).count(),
        }
        
        return Response(counts)

    @action(detail=False, methods=["post"])
    def send_reminders(self, request):
        """
        Send reminder notifications for appointments in a time range.
        Used by scheduled jobs.
        """
        start = parse_datetime(request.data.get("start")) or request.data.get("start")
        end = parse_datetime(request.data.get("end")) or request.data.get("end")

        if not start or not end:
            return Response({"detail": "start and end required"}, status=400)

        qs = self.get_queryset().filter(
            status=ApptStatus.SCHEDULED, start_at__gte=start, start_at__lte=end
        )

        n = 0
        for appt in qs:
            send_reminder(appt)
            if appt.patient and appt.patient.user_id:
                notify_user(
                    user=appt.patient.user,
                    topic="APPT_REMINDER",
                    title="Appointment Reminder",
                    body=f"Reminder: appointment at {appt.start_at}.",
                    data={"appointment_id": appt.id},
                    facility_id=appt.facility_id,
                )
            n += 1

        return Response({"sent": n})


# ─────────────────────────────────────────────────────────────
# Utility functions for syncing appointment status from encounters
# ─────────────────────────────────────────────────────────────

def sync_appointment_on_encounter_start(appointment_id: int):
    """
    Called when an encounter is started from an appointment.
    Updates appointment status to CHECKED_IN if still SCHEDULED.
    """
    try:
        appt = Appointment.objects.get(id=appointment_id)
        if appt.status == ApptStatus.SCHEDULED:
            appt.status = ApptStatus.CHECKED_IN
            appt.save(update_fields=["status", "updated_at"])
    except Appointment.DoesNotExist:
        pass


def sync_appointment_on_encounter_close(encounter_id: int):
    """
    Called when an encounter is closed.
    Updates linked appointment status to COMPLETED if not already terminal.
    """
    try:
        appt = Appointment.objects.filter(encounter_id=encounter_id).first()
        if appt and appt.status not in (
            ApptStatus.COMPLETED,
            ApptStatus.CANCELLED,
            ApptStatus.NO_SHOW,
        ):
            appt.status = ApptStatus.COMPLETED
            appt.save(update_fields=["status", "updated_at"])
    except Exception:
        pass
