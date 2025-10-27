from django.utils.dateparse import parse_datetime
from django.db.models import Q
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Encounter, EncounterAmendment
from .serializers import EncounterSerializer, EncounterListSerializer, AmendmentSerializer
from .permissions import IsStaff, CanViewEncounter
from .enums import EncounterStatus

class EncounterViewSet(viewsets.GenericViewSet,
                       mixins.CreateModelMixin,
                       mixins.RetrieveModelMixin,
                       mixins.UpdateModelMixin,
                       mixins.ListModelMixin):
    queryset = Encounter.objects.select_related("patient","facility","created_by").all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("list",):
            return EncounterListSerializer
        return EncounterSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user

        # patients see only their own encounters
        if getattr(u, "role", None) == "PATIENT":
            q = q.filter(patient__user_id=u.id)
        else:
            # staff limited by facility
            if getattr(u, "facility_id", None):
                q = q.filter(facility_id=u.facility_id)

        # filters
        patient_id = self.request.query_params.get("patient")
        if patient_id:
            q = q.filter(patient_id=patient_id)

        status_ = self.request.query_params.get("status")
        if status_:
            q = q.filter(status=status_)

        s = self.request.query_params.get("s")
        if s:
            q = q.filter(Q(chief_complaint__icontains=s) | Q(diagnoses__icontains=s) | Q(plan__icontains=s))

        start = self.request.query_params.get("start")
        end   = self.request.query_params.get("end")
        if start:
            q = q.filter(occurred_at__gte=parse_datetime(start) or start)
        if end:
            q = q.filter(occurred_at__lte=parse_datetime(end) or end)

        return q

    # Create: staff only
    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewEncounter]
        self.check_object_permissions(request, obj)
        ser = EncounterSerializer(obj, context={"request": request})
        return Response(ser.data)

    def update(self, request, *args, **kwargs):
        # only staff may update; serializer enforces the immutability
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def amend(self, request, pk=None):
        """
        Create an amendment when encounter is locked.
        payload: { "reason": "...", "content": "..." }
        """
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        if not enc.is_locked:
            return Response({"detail": "Encounter is not locked; edit the encounter instead."}, status=400)

        s = AmendmentSerializer(data=request.data, context={"request": request})
        s.is_valid(raise_exception=True)
        obj = s.save(encounter=enc)
        return Response(AmendmentSerializer(obj).data, status=201)

    @action(detail=True, methods=["get"])
    def amendments(self, request, pk=None):
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewEncounter]
        self.check_object_permissions(request, enc)
        qs = EncounterAmendment.objects.filter(encounter=enc).order_by("created_at")
        return Response(AmendmentSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """
        Close encounter (no further edits even within window).
        """
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        if enc.status == EncounterStatus.CLOSED:
            return Response({"detail":"Already closed"}, status=400)
        enc.status = EncounterStatus.CLOSED
        enc.save(update_fields=["status","updated_at"])
        return Response({"ok": True})

    @action(detail=True, methods=["post"])
    def cross_out(self, request, pk=None):
        """
        Cross-out an encounter: mark as CROSSED_OUT and prevent further clinical edits.
        It remains visible for audit/history.
        """
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        # ensure lock is applied if window elapsed, then cross out
        enc.maybe_lock()
        enc.status = EncounterStatus.CROSSED_OUT
        enc.save(update_fields=["status","locked_at","updated_at"])
        return Response({"detail": "Encounter crossed out.", "status": enc.status})
