from datetime import datetime
from django.utils.dateparse import parse_datetime
from django.db.models import Count
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import VitalSign
from .serializers import VitalSignSerializer, VitalSignListSerializer, VitalSummarySerializer
from .permissions import IsStaff, CanViewVitals

class VitalSignViewSet(viewsets.GenericViewSet,
                       mixins.CreateModelMixin,
                       mixins.RetrieveModelMixin,
                       mixins.ListModelMixin):
    queryset = VitalSign.objects.select_related("patient","facility","recorded_by").all()
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("list","latest","summary"):
            return VitalSignListSerializer
        return VitalSignSerializer

    # CREATE: staff only
    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    def get_queryset(self):
        q = self.queryset
        u = self.request.user

        # Patients: see only their own records
        if u.is_authenticated and u.role == "PATIENT":
            return q.filter(patient__user_id=u.id)

        # Staff: scope to facility
        if u.is_authenticated and u.facility_id:
            q = q.filter(facility_id=u.facility_id)

        # Filters
        patient_id = self.request.query_params.get("patient")
        if patient_id:
            q = q.filter(patient_id=patient_id)

        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        if start:
            dt = parse_datetime(start) or start  # allow date-only
            q = q.filter(measured_at__gte=dt)
        if end:
            dt = parse_datetime(end) or end
            q = q.filter(measured_at__lte=dt)

        return q

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewVitals]
        self.check_object_permissions(request, obj)
        ser = VitalSignSerializer(obj)
        return Response(ser.data)

    @action(detail=False, methods=["get"])
    def latest(self, request):
        """
        Latest vital per patient OR latest for a specific patient (?patient=ID)
        """
        q = self.get_queryset()
        pid = request.query_params.get("patient")
        if pid:
            obj = q.filter(patient_id=pid).order_by("-measured_at","-id").first()
            if not obj:
                return Response({"detail":"No vitals"}, status=404)
            return Response(VitalSignListSerializer(obj).data)

        # latest per patient in scope
        latest_map = {}
        for v in q.order_by("patient_id","-measured_at","-id"):
            if v.patient_id not in latest_map:
                latest_map[v.patient_id] = v
        return Response(VitalSignListSerializer(latest_map.values(), many=True).data)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """
        Counts by flag in current scope, optional patient filter.
        """
        q = self.get_queryset()
        agg = q.values("overall").annotate(c=Count("id"))
        by = {a["overall"]: a["c"] for a in agg}
        latest = q.order_by("-measured_at","-id").first()
        data = {
            "total": q.count(),
            "green": by.get("GREEN", 0),
            "yellow": by.get("YELLOW", 0),
            "red": by.get("RED", 0),
            "latest_overall": latest.overall if latest else None,
        }
        s = VitalSummarySerializer(data)
        return Response(s.data)
