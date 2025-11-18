import csv, io
from django.utils.dateparse import parse_datetime
from django.db.models import Q
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import ImagingProcedure, ImagingRequest, ImagingReport, ImagingAsset
from .serializers import (
    ImagingProcedureSerializer,
    ImagingRequestCreateSerializer, ImagingRequestReadSerializer,
    ImagingReportSerializer, ImagingAssetSerializer
)
from .permissions import IsStaff, CanViewRequest
from .enums import RequestStatus
from .services.notify import notify_report_ready
from notifications.services.notify import notify_user

class ProcedureViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin):
    queryset = ImagingProcedure.objects.filter(is_active=True).order_by("name")
    serializer_class = ImagingProcedureSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def import_csv(self, request):
        """
        CSV columns: code,name,modality,price
        """
        f = request.FILES.get("file")
        if not f:
            return Response({"detail":"file is required"}, status=400)
        buf = io.StringIO(f.read().decode("utf-8"))
        reader = csv.DictReader(buf)
        created, updated = 0, 0
        for row in reader:
            code = (row.get("code") or "").strip()
            if not code:
                continue
            defaults = {
                "name": (row.get("name") or "").strip(),
                "modality": (row.get("modality") or "XR").strip(),
                "price": (row.get("price") or 0),
                "is_active": True,
            }
            obj, is_created = ImagingProcedure.objects.update_or_create(code=code, defaults=defaults)
            created += int(is_created)
            updated += int(not is_created)
        return Response({"created": created, "updated": updated})

class ImagingRequestViewSet(viewsets.GenericViewSet,
                            mixins.CreateModelMixin,
                            mixins.RetrieveModelMixin,
                            mixins.ListModelMixin):
    queryset = ImagingRequest.objects.select_related("patient","facility","requested_by","procedure").prefetch_related("report","report__assets")
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create",):
            return ImagingRequestCreateSerializer
        return ImagingRequestReadSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user

        if u.role == "PATIENT":
            q = q.filter(patient__user_id=u.id)
        elif u.facility_id:
            q = q.filter(facility_id=u.facility_id)

        # filters
        patient_id = self.request.query_params.get("patient")
        if patient_id:
            q = q.filter(patient_id=patient_id)

        status_ = self.request.query_params.get("status")
        if status_:
            q = q.filter(status=status_)

        modality = self.request.query_params.get("modality")
        if modality:
            q = q.filter(procedure__modality=modality)

        start = self.request.query_params.get("start")
        end   = self.request.query_params.get("end")
        if start:
            q = q.filter(requested_at__gte=parse_datetime(start) or start)
        if end:
            q = q.filter(requested_at__lte=parse_datetime(end) or end)

        s = self.request.query_params.get("s")
        if s:
            q = q.filter(Q(indication__icontains=s) | Q(procedure__name__icontains=s) | Q(procedure__code__icontains=s))

        return q

    # Create: staff only
    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewRequest]
        self.check_object_permissions(request, obj)
        return Response(ImagingRequestReadSerializer(obj).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def schedule(self, request, pk=None):
        """
        Set scheduled_for and move status to SCHEDULED
        payload: { "scheduled_for": "2025-10-16T10:00:00Z" }
        """
        req = self.get_object()
        dt = request.data.get("scheduled_for")
        if not dt:
            return Response({"detail":"scheduled_for required"}, status=400)
        req.scheduled_for = parse_datetime(dt) or dt
        req.status = RequestStatus.SCHEDULED
        req.save(update_fields=["scheduled_for","status"])
        return Response({"ok": True})

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def report(self, request, pk=None):
        """
        Create final report; optional file uploads via multipart.
        fields: findings, impression, (files[])
        """
        req = self.get_object()
        if req.status == RequestStatus.CANCELLED:
            return Response({"detail":"Request is cancelled"}, status=400)

        # create/replace report
        rep = getattr(req, "report", None)
        if rep:
            rep.findings = request.data.get("findings", rep.findings)
            rep.impression = request.data.get("impression", rep.impression)
            rep.reported_by = request.user
            rep.save()
        else:
            rep = ImagingReport.objects.create(
                request=req,
                reported_by=request.user,
                findings=request.data.get("findings",""),
                impression=request.data.get("impression",""),
            )

        # handle assets
        files = request.FILES.getlist("files")
        for f in files:
            ImagingAsset.objects.create(report=rep, kind=f.content_type or "", file=f)

        # update status
        req.status = RequestStatus.REPORTED
        req.save(update_fields=["status"])

        # notify patient (non-blocking)
        if req.patient and req.patient.email:
            notify_report_ready(req.patient.email, req.id)
        
        if req.patient and req.patient.user_id:
            notify_user(
                user=req.patient.user,
                topic="IMAGING_REPORT_READY",
                title="Your imaging report is ready",
                body=f"Report for {req.procedure.name} is available.",
                data={"request_id": req.id},
                facility_id=req.facility_id,
            )

        return Response(ImagingReportSerializer(rep).data, status=201)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def statuses(self, request):
        return Response([c for c,_ in RequestStatus.choices])
