from django.utils.dateparse import parse_datetime
from django.db.models import Q
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import Appointment
from .serializers import AppointmentSerializer, AppointmentUpdateSerializer
from .permissions import IsStaff, CanViewAppointment
from .enums import ApptStatus
from .services.notify import send_confirmation, send_reminder
from notifications.services.notify import notify_user


class AppointmentViewSet(viewsets.GenericViewSet,
                         mixins.CreateModelMixin,
                         mixins.RetrieveModelMixin,
                         mixins.UpdateModelMixin,
                         mixins.ListModelMixin):
    queryset = Appointment.objects.select_related("patient", "facility", "provider", "created_by")
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return AppointmentUpdateSerializer
        return AppointmentSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user

        if u.role == "PATIENT":
            q = q.filter(patient__user_id=u.id)
        elif u.facility_id:
            q = q.filter(facility_id=u.facility_id)

        patient_id = self.request.query_params.get("patient")
        provider_id = self.request.query_params.get("provider")
        status_ = self.request.query_params.get("status")
        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        s = self.request.query_params.get("s")

        if patient_id:
            q = q.filter(patient_id=patient_id)
        if provider_id:
            q = q.filter(provider_id=provider_id)
        if status_:
            q = q.filter(status=status_)
        if start:
            q = q.filter(start_at__gte=parse_datetime(start) or start)
        if end:
            q = q.filter(end_at__lte=parse_datetime(end) or end)
        if s:
            q = q.filter(Q(reason__icontains=s) | Q(notes__icontains=s))

        return q.order_by("start_at", "id")

    # Create: staff can create for any patient in facility;
    # patient can only create for *self*
    def create(self, request, *args, **kwargs):
        user = request.user
        data = request.data.copy()

        if user.role == "PATIENT":
            # Attach to this user's patient profile automatically
            patient = getattr(user, "patient", None)
            if not patient:
                return Response(
                    {"detail": "No patient profile linked to this user."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Force patient field to their own id
            data["patient"] = patient.id
        else:
            # Staff (PROVIDER, ADMIN, etc.) must pass permission checks
            self.permission_classes = [IsAuthenticated, IsStaff]
            self.check_permissions(request)

        # Use standard serializer path (so facility, overlaps etc are handled)
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        resp = Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
            headers=self.get_success_headers(serializer.data),
        )

        # send confirmation email (best effort)
        try:
            appt = Appointment.objects.get(id=resp.data["id"])
            send_confirmation(appt)
        except Exception:
            pass

        return resp

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewAppointment]
        self.check_object_permissions(request, obj)
        return Response(AppointmentSerializer(obj).data)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        if request.user.role != "PATIENT":
            self.permission_classes = [IsAuthenticated, IsStaff]
            self.check_permissions(request)
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def check_in(self, request, pk=None):
        appt = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        if appt.status != ApptStatus.SCHEDULED:
            return Response({"detail": "Only scheduled appointments can be checked-in."}, status=400)
        appt.status = ApptStatus.CHECKED_IN
        appt.save(update_fields=["status", "updated_at"])
        return Response({"ok": True})

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        appt = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        if appt.status not in (ApptStatus.SCHEDULED, ApptStatus.CHECKED_IN):
            return Response({"detail": "Only scheduled/checked-in appointments can be completed."}, status=400)
        appt.status = ApptStatus.COMPLETED
        appt.save(update_fields=["status", "updated_at"])
        return Response({"ok": True})

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        appt = self.get_object()
        if request.user.role != "PATIENT":
            self.permission_classes = [IsAuthenticated, IsStaff]
            self.check_permissions(request)
        if appt.status in (ApptStatus.COMPLETED,):
            return Response({"detail": "Completed appointments cannot be cancelled."}, status=400)
        appt.status = ApptStatus.CANCELLED
        appt.save(update_fields=["status", "updated_at"])
        return Response({"ok": True})

    @action(detail=True, methods=["post"])
    def no_show(self, request, pk=None):
        appt = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        if appt.status != ApptStatus.SCHEDULED:
            return Response({"detail": "Only scheduled appointments can be marked no-show."}, status=400)
        appt.status = ApptStatus.NO_SHOW
        appt.save(update_fields=["status", "updated_at"])
        return Response({"ok": True})

    @action(detail=False, methods=["get"])
    def statuses(self, request):
        return Response([c for c, _ in ApptStatus.choices])

    @action(detail=False, methods=["post"])
    def send_reminders(self, request):
        start = parse_datetime(request.data.get("start")) or request.data.get("start")
        end = parse_datetime(request.data.get("end")) or request.data.get("end")
        if not start or not end:
            return Response({"detail": "start and end required"}, status=400)

        qs = self.get_queryset().filter(
            status=ApptStatus.SCHEDULED,
            start_at__gte=start,
            start_at__lte=end
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
