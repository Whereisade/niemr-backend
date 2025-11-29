from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

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
    authentication_classes = [JWTAuthentication]
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
    CRUD for dependents.

    Collection endpoints (scoped by role):

      - GET  /api/patients/dependents/
          * Patient users (guardians): only their own dependents.
          * Staff/clinical/admin: dependents within their facility.

      - POST /api/patients/dependents/
          * Patient users: create a dependent for themselves (as parent_patient).
          * Staff/clinical/admin: must send parent_patient_id in the payload.

    Item endpoints:

      - GET    /api/patients/dependents/{id}/
      - PATCH  /api/patients/dependents/{id}/
      - DELETE /api/patients/dependents/{id}/
    """

    queryset = Patient.objects.select_related("parent_patient").filter(
        parent_patient__isnull=False
    )
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsStaffOrGuardianForDependent]

    def get_serializer_class(self):
        # Use the create serializer when creating a new dependent
        if self.action == "create":
            return DependentCreateSerializer

        # Use the update serializer for partial/full updates
        if self.action in ("update", "partial_update"):
            return DependentUpdateSerializer

        # Default for list/retrieve is the detail serializer
        return DependentDetailSerializer

    def get_queryset(self):
        qs = self.queryset
        user = self.request.user

        # Staff / clinical / admin users:
        if (
            getattr(user, "is_staff", False)
            or getattr(user, "is_clinician", False)
            or getattr(user, "is_admin", False)
        ):
            # If user is tied to a facility, scope to that facility
            if getattr(user, "facility_id", None):
                return qs.filter(parent_patient__facility_id=user.facility_id)
            return qs

        # Guardian (patient user): only dependents where they are the parent_patient
        patient_profile = getattr(user, "patient_profile", None)
        if patient_profile:
            # NOTE: this fixes the bug: parent_parent_id -> parent_patient_id
            return qs.filter(parent_patient_id=patient_profile.id)

        # No access otherwise
        return qs.none()

    def create(self, request, *args, **kwargs):
        """
        Create a new dependent.

        - For patient guardians:
            POST /api/patients/dependents/
            payload: { first_name, last_name, dob?, gender? }

        - For staff/clinical/admin:
            POST /api/patients/dependents/
            payload: { parent_patient_id, first_name, last_name, dob?, gender? }
        """
        user = request.user

        # Determine parent_patient based on role
        parent_patient = None

        # Staff / clinical / admin: expect explicit parent_patient_id
        if (
            getattr(user, "is_staff", False)
            or getattr(user, "is_clinician", False)
            or getattr(user, "is_admin", False)
        ):
            parent_id = request.data.get("parent_patient_id")
            if not parent_id:
                return Response(
                    {
                        "parent_patient_id": [
                            "This field is required for staff-created dependents."
                        ]
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            parent_patient = get_object_or_404(Patient, pk=parent_id)

        else:
            # Patient / guardian path – attach to their own patient profile
            patient_profile = getattr(user, "patient_profile", None)
            if not patient_profile:
                return Response(
                    {
                        "detail": "You do not have a patient profile to attach dependents to."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            parent_patient = patient_profile

        # Validate basic dependent fields
        create_serializer = DependentCreateSerializer(data=request.data)
        create_serializer.is_valid(raise_exception=True)

        # Create the dependent Patient instance
        dependent = Patient(
            **create_serializer.validated_data,
            parent_patient=parent_patient,
        )
        # Enforce model validation (clean) before save
        dependent.full_clean()
        dependent.save()

        # Return detail representation
        detail = DependentDetailSerializer(
            dependent, context={"request": request}
        )
        headers = self.get_success_headers(detail.data)
        return Response(detail.data, status=status.HTTP_201_CREATED, headers=headers)

# patch the existing PatientViewSet to add nested dependents action
def add_dependents_actions_to_patient_viewset(viewset_cls):
    """
    Dynamically attach nested dependents actions to PatientViewSet:

      - GET  /api/patients/{pk}/dependents/
      - POST /api/patients/{pk}/dependents/

    This is mainly for facility / staff flows where you want to see or add
    dependents for a specific patient from the patient detail context.
    """

    @action(detail=True, methods=["get"], url_path="dependents")
    def dependents(self, request, pk=None):
        # List dependents whose parent_patient is this patient
        qs = (
            Patient.objects.select_related("parent_patient")
            .filter(parent_patient_id=pk)
            .order_by("-id")
        )
        serializer = DependentDetailSerializer(
            qs, many=True, context={"request": request}
        )
        return Response(serializer.data)

    @dependents.mapping.post
    def add_dependent(self, request, pk=None):
        # Create a new dependent for this patient (used mainly by staff)
        parent_patient = get_object_or_404(Patient, pk=pk)

        create_serializer = DependentCreateSerializer(data=request.data)
        create_serializer.is_valid(raise_exception=True)

        dependent = Patient(
            **create_serializer.validated_data,
            parent_patient=parent_patient,
        )
        dependent.full_clean()
        dependent.save()

        detail = DependentDetailSerializer(
            dependent, context={"request": request}
        )
        return Response(detail.data, status=status.HTTP_201_CREATED)

    # Attach the actions to the given viewset class
    setattr(viewset_cls, "dependents", dependents)
    setattr(viewset_cls, "add_dependent", add_dependent)

    return viewset_cls

PatientViewSet = add_dependents_actions_to_patient_viewset(PatientViewSet)
