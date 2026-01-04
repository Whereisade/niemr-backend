from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, mixins, status, permissions
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import ValidationError

from .models import Patient, PatientDocument, HMO, Allergy, PatientProviderLink
from .serializers import (
    PatientSerializer, PatientCreateByStaffSerializer, PatientDocumentSerializer,
    HMOSerializer, SelfRegisterSerializer,
    DependentCreateSerializer, DependentSerializer, DependentUpdateSerializer,
    AllergySerializer, AllergyCreateSerializer, AllergyUpdateSerializer,
)
from .permissions import IsSelfOrFacilityStaff, IsStaff, IsStaffOrGuardianForDependent, IsStaffOrSelfPatient
from accounts.enums import UserRole
from .enums import InsuranceStatus

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
        u = request.user

        # Facility staff: scope to their facility patients
        if getattr(u, "facility_id", None):
            q = q.filter(facility_id=u.facility_id)
        else:
            # Independent staff users (no facility) must NOT see all patients.
            # Scope to patients they are related to via appointments/encounters/labs/prescriptions.
            role = (getattr(u, "role", "") or "").upper()
            if role not in {"SUPER_ADMIN", "ADMIN"}:
                uid = getattr(u, "id", None)
                if uid:
                    q = q.filter(
                        Q(appointments__provider_id=uid)
                        | Q(encounters__created_by_id=uid)
                        | Q(encounters__provider_id=uid)
                        | Q(encounters__nurse_id=uid)
                        | Q(lab_orders__ordered_by_id=uid)
                        | Q(lab_orders__outsourced_to_id=uid)
                        | Q(prescriptions__prescribed_by_id=uid)
                        | Q(prescriptions__outsourced_to_id=uid)
                        | Q(provider_links__provider_id=uid)
                    ).distinct()

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

    def perform_create(self, serializer):
        """On independent provider create, auto-link patient to the creator.

        This prevents the provider workspace from having to auto-start an encounter
        just to make the new patient visible in the provider patient list.
        """

        patient = serializer.save()
        u = self.request.user
        # For independent provider staff (no facility), link the created patient
        if not getattr(u, "facility_id", None):
            role = (getattr(u, "role", "") or "").upper()
            if role not in {"SUPER_ADMIN", "ADMIN"} and role in {
                "DOCTOR",
                "NURSE",
                "LAB",
                "PHARMACY",
                "FRONTDESK",
            }:
                PatientProviderLink.objects.get_or_create(patient=patient, provider=u)
        return patient

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsSelfOrFacilityStaff])
    def upload_document(self, request, pk=None):
        patient = self.get_object()
        s = PatientDocumentSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        user = request.user
        role_value = getattr(user, "role", None)
        uploaded_by_role = (
            str(role_value).upper()
            if role_value and str(role_value).upper() in PatientDocument.UploadedBy.values
            else PatientDocument.UploadedBy.SYSTEM
        )

        obj = s.save(
            patient=patient,
            uploaded_by=user,
            uploaded_by_role=uploaded_by_role,
        )
        return Response(PatientDocumentSerializer(obj).data, status=201)


@action(detail=False, methods=["get"], permission_classes=[IsAuthenticated, IsStaff])
def hmos(self, request):
    """List active HMOs for the requester's facility."""
    facility_id = getattr(request.user, "facility_id", None)
    if not facility_id:
        return Response([])
    qs = HMO.objects.filter(facility_id=facility_id, is_active=True).order_by("name")
    return Response(HMOSerializer(qs, many=True).data)

@action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
def seed_hmos(self, request):
    """Bulk-create HMOs for the requester's facility (SUPER_ADMIN only)."""
    role = (getattr(request.user, "role", "") or "").upper()
    if role != UserRole.SUPER_ADMIN:
        return Response({"detail": "Only facility SUPER_ADMIN can create HMOs."}, status=403)

    facility_id = getattr(request.user, "facility_id", None)
    if not facility_id:
        return Response({"detail": "User must belong to a facility."}, status=400)

    names = request.data.get("names") or []
    created = []
    for n in names:
        n = (n or "").strip()
        if not n:
            continue
        obj, was_created = HMO.objects.get_or_create(
            facility_id=facility_id,
            name=n,
            defaults={"is_active": True},
        )
        if was_created:
            created.append(obj.name)
    return Response({"created": created}, status=201)

@action(detail=True, methods=["post"], url_path="attach-hmo", permission_classes=[IsAuthenticated, IsStaff])
def attach_hmo(self, request, pk=None):
    """Attach a patient to an HMO within the same facility."""
    patient = self.get_object()

    user_facility_id = getattr(request.user, "facility_id", None)
    if not user_facility_id or patient.facility_id != user_facility_id:
        return Response({"detail": "Patient must belong to your facility."}, status=403)

    hmo_id = request.data.get("hmo_id")
    if not hmo_id:
        return Response({"detail": "hmo_id is required"}, status=400)

    hmo = get_object_or_404(HMO, id=hmo_id, facility_id=user_facility_id, is_active=True)

    patient.hmo = hmo
    patient.insurance_status = InsuranceStatus.INSURED
    patient.save(update_fields=["hmo", "insurance_status", "updated_at"])
    return Response(PatientSerializer(patient, context={"request": request}).data)

@action(detail=True, methods=["post"], url_path="clear-hmo", permission_classes=[IsAuthenticated, IsStaff])
def clear_hmo(self, request, pk=None):
    """Remove a patient's HMO attachment (marks as uninsured)."""
    patient = self.get_object()

    user_facility_id = getattr(request.user, "facility_id", None)
    if not user_facility_id or patient.facility_id != user_facility_id:
        return Response({"detail": "Patient must belong to your facility."}, status=403)

    patient.hmo = None
    patient.insurance_status = InsuranceStatus.UNINSURED
    patient.save(update_fields=["hmo", "insurance_status", "updated_at"])
    return Response(PatientSerializer(patient, context={"request": request}).data)

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
    CRUD for dependents (Patients with parent_patient set).

    Collection endpoints:

      - GET  /api/patients/dependents/
      - POST /api/patients/dependents/

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
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return DependentCreateSerializer
        if self.action in ("update", "partial_update"):
            return DependentUpdateSerializer
        return DependentSerializer

    def get_queryset(self):
        qs = self.queryset
        user = self.request.user

        # Staff / clinical / admin → scope by facility if available
        if (
            getattr(user, "is_staff", False)
            or getattr(user, "is_clinician", False)
            or getattr(user, "is_admin", False)
        ):
            if getattr(user, "facility_id", None):
                return qs.filter(parent_patient__facility_id=user.facility_id)
            return qs

        # Patient user / guardian → only their own dependents
        patient_profile = getattr(user, "patient_profile", None)
        if patient_profile:
            return qs.filter(parent_patient_id=patient_profile.id)

        return qs.none()

    def perform_create(self, serializer):
        """
        Attach the correct parent_patient based on who is logged in,
        and propagate guardian_user + facility.
        """
        user = self.request.user

        # Staff/admin path → expect explicit parent_patient_id
        if (
            getattr(user, "is_staff", False)
            or getattr(user, "is_clinician", False)
            or getattr(user, "is_admin", False)
        ):
            parent_id = self.request.data.get("parent_patient_id")
            if not parent_id:
                raise ValidationError(
                    {
                        "parent_patient_id": [
                            "This field is required when staff create a dependent."
                        ]
                    }
                )
            parent_patient = get_object_or_404(Patient, pk=parent_id)

        else:
            # Patient/guardian path → attach to their own patient profile
            patient_profile = getattr(user, "patient_profile", None)
            if not patient_profile:
                raise ValidationError(
                    {
                        "detail": "You do not have a patient profile to attach dependents to."
                    }
                )
            parent_patient = patient_profile

        # guardian_user = current user only if they are a patient/guardian
        guardian_user = user if getattr(user, "patient_profile", None) else None

        # facility: prefer user's facility, fall back to parent's facility
        facility = getattr(user, "facility", None)
        if facility is None and getattr(parent_patient, "facility_id", None):
            facility = parent_patient.facility

        serializer.save(
            parent_patient=parent_patient,
            guardian_user=guardian_user,
            facility=facility,
        )


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
        serializer = DependentSerializer(
            qs, many=True, context={"request": request}
        )
        return Response(serializer.data)

    @dependents.mapping.post
    def add_dependent(self, request, pk=None):
        # Create a new dependent for this patient (used mainly by staff)
        parent_patient = get_object_or_404(Patient, pk=pk)

        create_serializer = DependentCreateSerializer(data=request.data)
        create_serializer.is_valid(raise_exception=True)

        # guardian_user: staff may leave this null; patient users can be guardian
        user = request.user
        guardian_user = user if getattr(user, "patient_profile", None) else None

        # facility: prefer parent's facility
        facility = getattr(parent_patient, "facility", None)

        dependent = create_serializer.save(
            parent_patient=parent_patient,
            guardian_user=guardian_user,
            facility=facility,
        )

        detail = DependentSerializer(
            dependent, context={"request": request}
        )
        return Response(detail.data, status=status.HTTP_201_CREATED)

    # Attach the actions to the given viewset class
    setattr(viewset_cls, "dependents", dependents)
    setattr(viewset_cls, "add_dependent", add_dependent)

    return viewset_cls

PatientViewSet = add_dependents_actions_to_patient_viewset(PatientViewSet)

class PatientDocumentViewSet(viewsets.ModelViewSet):
    """
    Patient-attached documents (lab results, imaging, prescriptions, etc.).

    - PATIENT role:
        * GET /api/patients/documents/ → own documents
        * POST /api/patients/documents/ → upload to own record
        * DELETE /api/patients/documents/{id}/ → delete own doc (optional)
    - Staff (DOCTOR/NURSE/etc):
        * GET /api/patients/documents/?patient=<patient_id>
          → all docs for that patient, regardless of facility
    """

    serializer_class = PatientDocumentSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = PatientDocument.objects.select_related("patient", "uploaded_by")

        # Patient login → only own documents
        if getattr(user, "role", None) == UserRole.PATIENT:
            patient = getattr(user, "patient_profile", None)
            if patient is None:
                return qs.none()
            return qs.filter(patient=patient)

        # Staff → must explicitly ask for a patient
        patient_id = self.request.query_params.get("patient")
        if patient_id:
            return qs.filter(patient_id=patient_id)

        # No patient filter → don't leak anything
        return qs.none()

    def create(self, request, *args, **kwargs):
        """
        Override create to handle patient assignment before validation.
        """
        user = request.user
        
        # Determine which patient this document belongs to
        patient = None
        
        if getattr(user, "role", None) == UserRole.PATIENT:
            # Patient uploads to their own record
            patient = getattr(user, "patient_profile", None)
            if patient is None:
                return Response(
                    {"detail": "No patient profile linked to this user."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            # Staff upload - get patient from request
            patient_id = request.data.get("patient")
            if not patient_id:
                return Response(
                    {"patient": "This field is required when staff upload on behalf of a patient."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            try:
                patient = Patient.objects.get(id=patient_id)
            except Patient.DoesNotExist:
                return Response(
                    {"patient": "Patient not found."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Now validate and create the document
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Determine uploaded_by_role
        role_value = getattr(user, "role", None)
        if getattr(user, "role", None) == UserRole.PATIENT:
            uploaded_by_role = PatientDocument.UploadedBy.PATIENT
        else:
            uploaded_by_role = self._guess_uploaded_by_role(user)
        
        # Save with patient
        document = serializer.save(
            patient=patient,
            uploaded_by=user,
            uploaded_by_role=uploaded_by_role,
        )
        
        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
            headers=headers
        )

    def _guess_uploaded_by_role(self, user):
        role = (getattr(user, "role", "") or "").upper()
        if role in PatientDocument.UploadedBy.values:
            return role
        return PatientDocument.UploadedBy.SYSTEM


# --- Allergy ViewSet ---

class AllergyViewSet(viewsets.ModelViewSet):
    """
    CRUD for patient allergies.
    
    Endpoints:
      - GET    /api/patients/allergies/           → List allergies (patient: own, staff: by patient param)
      - POST   /api/patients/allergies/           → Create allergy
      - GET    /api/patients/allergies/{id}/      → Retrieve allergy
      - PATCH  /api/patients/allergies/{id}/      → Update allergy
      - DELETE /api/patients/allergies/{id}/      → Delete allergy
    
    Query params (for staff):
      - patient: Filter by patient ID
      - is_active: Filter by active status (true/false)
      - allergy_type: Filter by type (DRUG, FOOD, etc.)
      - severity: Filter by severity (MILD, MODERATE, SEVERE, LIFE_THREATENING)
    """
    
    queryset = Allergy.objects.select_related("patient", "recorded_by").all()
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    
    def get_serializer_class(self):
        if self.action == "create":
            return AllergyCreateSerializer
        if self.action in ("update", "partial_update"):
            return AllergyUpdateSerializer
        return AllergySerializer
    
    def get_queryset(self):
        user = self.request.user
        qs = self.queryset
        
        # Patient login → only own allergies
        if getattr(user, "role", None) == UserRole.PATIENT:
            patient = getattr(user, "patient_profile", None)
            if patient is None:
                return qs.none()
            return qs.filter(patient=patient).order_by("-created_at")
        
        # Staff → filter by patient param or facility scope
        patient_id = self.request.query_params.get("patient")
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        elif getattr(user, "facility_id", None):
            # Scope to facility's patients
            qs = qs.filter(patient__facility_id=user.facility_id)
        
        # Additional filters
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == "true")
        
        allergy_type = self.request.query_params.get("allergy_type")
        if allergy_type:
            qs = qs.filter(allergy_type=allergy_type.upper())
        
        severity = self.request.query_params.get("severity")
        if severity:
            qs = qs.filter(severity=severity.upper())
        
        return qs.order_by("-created_at")
    
    def create(self, request, *args, **kwargs):
        """
        Create a new allergy record.
        Patient is determined by:
          - PATIENT role: own patient profile
          - Staff: explicit patient ID in request data
        """
        user = request.user
        patient = None
        
        if getattr(user, "role", None) == UserRole.PATIENT:
            # Patient creates for themselves
            patient = getattr(user, "patient_profile", None)
            if patient is None:
                return Response(
                    {"detail": "No patient profile linked to this user."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            # Staff creates for a specific patient
            patient_id = request.data.get("patient")
            if not patient_id:
                return Response(
                    {"patient": "This field is required when staff create an allergy."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            try:
                patient = Patient.objects.get(id=patient_id)
            except Patient.DoesNotExist:
                return Response(
                    {"patient": "Patient not found."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Check for duplicate allergen
        allergen = serializer.validated_data.get("allergen", "").strip()
        if Allergy.objects.filter(
            patient=patient,
            allergen__iexact=allergen,
            is_active=True
        ).exists():
            return Response(
                {"allergen": "This allergy is already recorded for this patient."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        allergy = serializer.save(
            patient=patient,
            recorded_by=user,
        )
        
        output_serializer = AllergySerializer(allergy)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)
    
    def update(self, request, *args, **kwargs):
        """
        Update an allergy record.
        """
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        
        # Check for duplicate allergen if changing
        new_allergen = serializer.validated_data.get("allergen")
        if new_allergen and new_allergen.strip().lower() != instance.allergen.lower():
            if Allergy.objects.filter(
                patient=instance.patient,
                allergen__iexact=new_allergen.strip(),
                is_active=True
            ).exclude(pk=instance.pk).exists():
                return Response(
                    {"allergen": "This allergy is already recorded for this patient."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        serializer.save()
        
        output_serializer = AllergySerializer(instance)
        return Response(output_serializer.data)
    
    def destroy(self, request, *args, **kwargs):
        """
        Delete an allergy record.
        """
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)