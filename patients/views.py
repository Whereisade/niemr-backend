from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from .models import Patient, PatientDocument, HMO
from .serializers import (
    PatientSerializer, PatientCreateByStaffSerializer, PatientDocumentSerializer,
    HMOSerializer, SelfRegisterSerializer,
    DependentCreateSerializer, DependentDetailSerializer, DependentUpdateSerializer,
)
from .permissions import IsSelfOrFacilityStaff, IsStaff, IsStaffOrGuardianForDependent, IsStaffOrSelfPatient
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

# --- Dependent endpoints / viewset below ---

class DependentViewSet(viewsets.ModelViewSet):
    """
    CRUD for individual dependents:
      GET    /api/patients/dependents/{id}/
      PATCH  /api/patients/dependents/{id}/
      DELETE /api/patients/dependents/{id}/
    """
    queryset = Patient.objects.select_related("parent_patient").all()
    permission_classes = [IsAuthenticated, IsStaffOrGuardianForDependent]

    def get_serializer_class(self):
        if self.request.method in ("PATCH", "PUT"):
            return DependentUpdateSerializer
        return DependentDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset().filter(parent_patient__isnull=False)
        user = self.request.user
        
        # Staff users should only see dependents from their facility
        if getattr(user, "is_staff", False) or getattr(user, "is_clinician", False) or getattr(user, "is_admin", False):
            if getattr(user, "facility_id", None):
                return qs.filter(parent_patient__facility_id=user.facility_id)
            return qs

        # Guardian (patient user) 
        patient_profile = getattr(user, "patient_profile", None)
        if patient_profile:
            return qs.filter(parent_parent_id=patient_profile.id)

        # No access otherwise
        return qs.none()


def add_dependents_actions_to_patient_viewset(PatientViewSet):
    @action(detail=True, methods=["get", "post"], url_path="dependents", permission_classes=[IsStaffOrSelfPatient])
    def dependents(self, request, pk=None):
        parent_patient = get_object_or_404(Patient, pk=pk)

        if request.method.lower() == "get":
            qs = Patient.objects.filter(parent_patient=parent_patient)
            serializer = DependentDetailSerializer(qs, many=True)
            return Response(serializer.data)

        # POST create
        serializer = DependentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        dependent = Patient(
            **serializer.validated_data,
            parent_patient=parent_patient,
        )
        # enforce model validation (clean) before save
        dependent.full_clean()
        dependent.save()

        out = DependentDetailSerializer(dependent)
        return Response(out.data, status=status.HTTP_201_CREATED)

    setattr(PatientViewSet, "dependents", dependents)
    return PatientViewSet

# patch the existing PatientViewSet to add nested dependents action
PatientViewSet = add_dependents_actions_to_patient_viewset(PatientViewSet)
