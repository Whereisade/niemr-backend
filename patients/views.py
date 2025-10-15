from django.db.models import Q
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from .models import Patient, PatientDocument, HMO
from .serializers import (
    PatientSerializer, PatientCreateByStaffSerializer, PatientDocumentSerializer,
    HMOSerializer, SelfRegisterSerializer
)
from .permissions import IsSelfOrFacilityStaff, IsStaff
from accounts.enums import UserRole

class PatientViewSet(viewsets.GenericViewSet,
                     mixins.CreateModelMixin,
                     mixins.RetrieveModelMixin,
                     mixins.UpdateModelMixin,
                     mixins.ListModelMixin):
    queryset = Patient.objects.select_related("user","facility","hmo").all().order_by("-created_at")
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create",):
            # staff creating patient in facility
            return PatientCreateByStaffSerializer
        return PatientSerializer

    def get_permissions(self):
        if self.action in ("list","create"):
            return [IsAuthenticated(), IsStaff()]
        elif self.action in ("retrieve","update","partial_update"):
            return [IsAuthenticated(), IsSelfOrFacilityStaff()]
        return super().get_permissions()

    def list(self, request, *args, **kwargs):
        q = self.queryset
        # staff can only see within their facility
        if request.user.facility_id:
            q = q.filter(facility_id=request.user.facility_id)
        # basic search
        s = request.query_params.get("s")
        if s:
            q = q.filter(
                Q(first_name__icontains=s) | Q(last_name__icontains=s) |
                Q(email__icontains=s) | Q(phone__icontains=s)
            )
        page = self.paginate_queryset(q)
        if page is not None:
            ser = PatientSerializer(page, many=True)
            return self.get_paginated_response(ser.data)
        ser = PatientSerializer(q, many=True)
        return Response(ser.data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsSelfOrFacilityStaff])
    def upload_document(self, request, pk=None):
        patient = self.get_object()
        s = PatientDocumentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        obj = s.save(patient=patient, uploaded_by_user=request.user)
        return Response(PatientDocumentSerializer(obj).data, status=201)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def hmos(self, request):
        qs = HMO.objects.all().order_by("name")
        return Response(HMOSerializer(qs, many=True).data)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def seed_hmos(self, request):
        names = request.data.get("names") or []
        created = []
        for n in names:
            n = n.strip()
            if not n:
                continue
            obj, was_created = HMO.objects.get_or_create(name=n)
            if was_created: created.append(obj.name)
        return Response({"created": created}, status=201)

@api_view(["POST"])
@permission_classes([AllowAny])
def self_register(request):
    """
    Public endpoint: create User(PATIENT) + Patient profile in one call.
    """
    s = SelfRegisterSerializer(data=request.data, context={"request": request})
    s.is_valid(raise_exception=True)
    patient = s.save()
    return Response({"patient_id": patient.id}, status=201)
