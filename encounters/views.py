from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from appointments.models import Appointment
from patients.models import Patient

from .enums import EncounterStage, EncounterStatus
from .models import Encounter, EncounterAmendment
from .permissions import CanViewEncounter, IsStaff
from .serializers import AmendmentSerializer, EncounterListSerializer, EncounterSerializer


class EncounterViewSet(
    viewsets.GenericViewSet,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
):
    queryset = Encounter.objects.select_related("patient", "facility", "created_by").all()
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        return EncounterListSerializer if self.action == "list" else EncounterSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user

        if u.role == "PATIENT":
            q = q.filter(patient__user_id=u.id)
        elif u.facility_id:
            q = q.filter(facility_id=u.facility_id)
        else:
            # independent staff: only what they created (admins can see all)
            role = (getattr(u, "role", "") or "").upper()
            if role not in {"ADMIN", "SUPER_ADMIN"}:
                q = q.filter(created_by_id=u.id)

        patient_id = self.request.query_params.get("patient")
        if patient_id:
            q = q.filter(patient_id=patient_id)

        status_ = self.request.query_params.get("status")
        if status_:
            q = q.filter(status=status_)

        stage_ = self.request.query_params.get("stage")
        if stage_:
            q = q.filter(stage=stage_)

        s = self.request.query_params.get("s")
        if s:
            q = q.filter(
                Q(chief_complaint__icontains=s)
                | Q(diagnoses__icontains=s)
                | Q(plan__icontains=s)
            )

        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        if start:
            q = q.filter(occurred_at__gte=parse_datetime(start) or start)
        if end:
            q = q.filter(occurred_at__lte=parse_datetime(end) or end)

        return q

    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewEncounter]
        self.check_object_permissions(request, obj)
        return Response(EncounterSerializer(obj, context={"request": request}).data)

    def update(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().update(request, *args, **kwargs)

    # ------------------------------------------------------------
    # Start flows
    # ------------------------------------------------------------
    @action(detail=False, methods=["post"], url_path="start-from-appointment")
    def start_from_appointment(self, request):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        appt_id = request.data.get("appointment_id")
        if not appt_id:
            return Response({"detail": "appointment_id is required"}, status=400)

        appt = Appointment.objects.select_related("patient", "facility").filter(id=appt_id).first()
        if not appt:
            return Response({"detail": "Appointment not found"}, status=404)

        # Facility staff can't start encounter from another facility's appointment
        if request.user.facility_id and appt.facility_id != request.user.facility_id:
            return Response({"detail": "Appointment is not in your facility"}, status=403)

        # Reuse existing link if present
        if appt.encounter_id:
            enc = Encounter.objects.filter(id=appt.encounter_id).first()
            if enc:
                return Response(EncounterSerializer(enc, context={"request": request}).data)

        enc = Encounter.objects.create(
            patient=appt.patient,
            facility=appt.facility,
            created_by=request.user,
            status=EncounterStatus.IN_PROGRESS,
            stage=EncounterStage.LABS,
            occurred_at=timezone.now(),
            appointment_id=appt.id,
            chief_complaint=getattr(appt, "reason", "") or "",
        )

        appt.encounter_id = enc.id
        appt.save(update_fields=["encounter_id", "updated_at"])

        return Response(EncounterSerializer(enc, context={"request": request}).data, status=201)

    @action(detail=False, methods=["post"], url_path="start-from-patient")
    def start_from_patient(self, request):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        patient_id = request.data.get("patient_id")
        if not patient_id:
            return Response({"detail": "patient_id is required"}, status=400)

        patient = Patient.objects.select_related("facility").filter(id=patient_id).first()
        if not patient:
            return Response({"detail": "Patient not found"}, status=404)

        if request.user.facility_id and patient.facility_id != request.user.facility_id:
            return Response({"detail": "Patient is not in your facility"}, status=403)

        enc = Encounter.objects.create(
            patient=patient,
            facility=request.user.facility if request.user.facility_id else patient.facility,
            created_by=request.user,
            status=EncounterStatus.IN_PROGRESS,
            stage=EncounterStage.LABS,
            occurred_at=timezone.now(),
        )
        return Response(EncounterSerializer(enc, context={"request": request}).data, status=201)

    # ------------------------------------------------------------
    # Lab wait controls
    # ------------------------------------------------------------
    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        enc.status = EncounterStatus.WAITING_LABS
        enc.stage = EncounterStage.WAITING_LABS
        enc.paused_at = timezone.now()
        enc.paused_by = request.user
        enc.save(update_fields=["status", "stage", "paused_at", "paused_by", "updated_at"])
        return Response(EncounterSerializer(enc, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        # resume jumps to SOAP note even if labs exist
        enc.status = EncounterStatus.IN_PROGRESS
        enc.stage = EncounterStage.NOTE
        enc.resumed_at = timezone.now()
        enc.resumed_by = request.user
        enc.save(update_fields=["status", "stage", "resumed_at", "resumed_by", "updated_at"])
        return Response(EncounterSerializer(enc, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="skip_labs")
    def skip_labs(self, request, pk=None):
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        enc.labs_skipped_at = timezone.now()
        enc.labs_skipped_by = request.user
        enc.status = EncounterStatus.IN_PROGRESS
        enc.stage = EncounterStage.NOTE
        enc.save(
            update_fields=[
                "labs_skipped_at",
                "labs_skipped_by",
                "status",
                "stage",
                "updated_at",
            ]
        )
        return Response(EncounterSerializer(enc, context={"request": request}).data)

    # ------------------------------------------------------------
    # Existing clinical actions + new finalize note
    # ------------------------------------------------------------
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        enc.status = EncounterStatus.CLOSED
        enc.save(update_fields=["status", "updated_at"])
        return Response({"detail": "Encounter closed."})

    @action(detail=True, methods=["post"])
    def cross_out(self, request, pk=None):
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        reason = (request.data.get("reason") or "").strip()
        if not reason:
            return Response({"detail": "reason is required"}, status=400)

        enc.status = EncounterStatus.CROSSED_OUT
        enc.save(update_fields=["status", "updated_at"])
        EncounterAmendment.objects.create(
            encounter=enc,
            added_by=request.user,
            reason=f"CROSSED OUT: {reason}",
            content="(Encounter crossed out)",
        )
        return Response({"detail": "Encounter crossed out."})

    @action(detail=True, methods=["post"])
    def amend(self, request, pk=None):
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        reason = (request.data.get("reason") or "").strip()
        content = (request.data.get("content") or "").strip()
        if not reason or not content:
            return Response({"detail": "reason and content are required"}, status=400)

        EncounterAmendment.objects.create(
            encounter=enc, added_by=request.user, reason=reason, content=content
        )
        return Response({"detail": "Amendment added."}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def amendments(self, request, pk=None):
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewEncounter]
        self.check_object_permissions(request, enc)

        qs = enc.amendments.select_related("added_by").order_by("-created_at")
        return Response(AmendmentSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"], url_path="finalize_note")
    def finalize_note(self, request, pk=None):
        """
        Marks the SOAP/Dx part as completed and starts the 24h lock timer.
        Also moves workflow to PRESCRIPTION stage.
        """
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        if not enc.clinical_finalized_at:
            enc.clinical_finalized_at = timezone.now()
            enc.clinical_finalized_by = request.user

        enc.stage = EncounterStage.PRESCRIPTION
        enc.save(update_fields=["clinical_finalized_at", "clinical_finalized_by", "stage", "updated_at"])
        return Response(EncounterSerializer(enc, context={"request": request}).data)
