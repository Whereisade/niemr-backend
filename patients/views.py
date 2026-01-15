from django.db.models import Q, Prefetch
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, mixins, status, permissions
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import ValidationError
from facilities.permissions_utils import has_facility_permission
from facilities.permissions import IsFacilityAdmin, IsFacilityStaff, IsFacilitySuperAdmin
from .models import Patient, PatientDocument, HMO, Allergy, PatientProviderLink
from .models import SystemHMO, HMOTier, FacilityHMO, PatientFacilityHMOApproval
from .serializers import (
    PatientSerializer, PatientCreateByStaffSerializer, PatientDocumentSerializer,
    HMOSerializer, SelfRegisterSerializer,
    DependentCreateSerializer, DependentSerializer, DependentUpdateSerializer,
    AllergySerializer, AllergyCreateSerializer, AllergyUpdateSerializer,
)
from .serializers import (
    SystemHMOSerializer,
    SystemHMOListSerializer,
    SystemHMOCreateSerializer,
    HMOTierSerializer,
    FacilityHMOSerializer,
    FacilityHMOCreateSerializer,
    FacilityHMOUpdateRelationshipSerializer,
    PatientAttachHMOSerializer,
    PatientTransferHMOApprovalSerializer,
    PatientFacilityHMOApprovalSerializer,
    PatientFacilityHMOApprovalCreateSerializer,
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
        if self.action == "create":
            return [IsAuthenticated(), IsStaff()]
        elif self.action == "list":
            # Allow both staff AND patients to list
            return [IsAuthenticated()]
        elif self.action in ("retrieve","update","partial_update"):
            return [IsAuthenticated(), IsSelfOrFacilityStaff()]
        return super().get_permissions()

    def list(self, request, *args, **kwargs):
        q = self.queryset
        u = request.user

        # 🔧 FIX: PATIENT role users see only their own record
        if getattr(u, "role", None) == UserRole.PATIENT:
            patient_profile = getattr(u, "patient_profile", None)
            if patient_profile:
                q = q.filter(id=patient_profile.id)
            else:
                q = q.none()
        # Facility staff: scope to their facility patients
        elif getattr(u, "facility_id", None):
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
        
        # Handle None values from JSON - use "or" to convert None to empty string
        insurance_number = (request.data.get("insurance_number") or "").strip()
        insurance_expiry = request.data.get("insurance_expiry") or None
        insurance_notes = (request.data.get("insurance_notes") or "").strip()

        if not hmo_id:
            return Response({"detail": "hmo_id is required"}, status=400)

        hmo = get_object_or_404(HMO, id=hmo_id, facility_id=user_facility_id, is_active=True)

        patient.hmo = hmo
        patient.insurance_status = InsuranceStatus.INSURED
        patient.insurance_number = insurance_number
        patient.insurance_expiry = insurance_expiry
        patient.insurance_notes = insurance_notes
        
        patient.save(update_fields=[
            "hmo", 
            "insurance_status", 
            "insurance_number",
            "insurance_expiry",
            "insurance_notes",
            "updated_at"
        ])
        
        return Response(PatientSerializer(patient, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="clear-hmo", permission_classes=[IsAuthenticated, IsStaff])
    def clear_hmo(self, request, pk=None):
        """Remove a patient's HMO attachment (marks as self-pay)."""
        patient = self.get_object()

        user_facility_id = getattr(request.user, "facility_id", None)
        if not user_facility_id or patient.facility_id != user_facility_id:
            return Response({"detail": "Patient must belong to your facility."}, status=403)

        patient.hmo = None
        patient.insurance_status = InsuranceStatus.SELF_PAY
        patient.insurance_number = ""
        patient.insurance_expiry = None
        patient.insurance_notes = ""
        
        patient.save(update_fields=[
            "hmo", 
            "insurance_status",
            "insurance_number",
            "insurance_expiry",
            "insurance_notes",
            "updated_at"
        ])
        
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

    @action(
        detail=True,
        methods=['post'],
        url_path='attach-system-hmo',
        permission_classes=[IsAuthenticated]
    )
    def attach_system_hmo(self, request, pk=None):
        """
        Attach a patient to a System HMO with tier selection.
        
        This replaces the old attach_hmo action.
        
        POST /api/patients/{id}/attach-system-hmo/
        
        Body:
        {
            "system_hmo_id": 1,
            "tier_id": 2,
            "insurance_number": "INS-123456",
            "insurance_expiry": "2025-12-31",
            "insurance_notes": "Optional notes"
        }
        """
        from .views_hmo import patient_attach_system_hmo
        return patient_attach_system_hmo(self, request, pk)
    
    @action(
        detail=True,
        methods=['post'],
        url_path='clear-system-hmo',
        permission_classes=[IsAuthenticated]
    )
    def clear_system_hmo(self, request, pk=None):
        """
        Remove a patient's HMO enrollment (marks as self-pay).
        
        POST /api/patients/{id}/clear-system-hmo/
        """
        from .views_hmo import patient_clear_system_hmo
        return patient_clear_system_hmo(self, request, pk)
    
    @action(
        detail=True,
        methods=['get'],
        url_path='check-hmo-transfer',
        permission_classes=[IsAuthenticated]
    )
    def check_hmo_transfer(self, request, pk=None):
        """
        Check if a patient needs HMO transfer approval at this facility.
        
        GET /api/patients/{id}/check-hmo-transfer/
        
        Returns:
        - needs_approval: bool
        - existing_enrollment: HMO details if patient has one
        - approval_status: If approval exists, its status
        """
        from .views_hmo import patient_check_hmo_transfer
        return patient_check_hmo_transfer(self, request, pk)
    
    @action(detail=True, methods=["post"], url_path="detach-hmo", permission_classes=[IsAuthenticated])
    def detach_hmo(self, request, pk=None):
        """Allow patients to detach themselves from their HMO (self-service)."""
        patient = self.get_object()
        user = request.user

        # Patients can only detach their own profile
        if user.role == UserRole.PATIENT:
            patient_profile = getattr(user, "patient_profile", None)
            if not patient_profile or patient.id != patient_profile.id:
                return Response(
                    {"detail": "You can only detach your own HMO coverage."},
                    status=403
                )
        # Staff can detach for any patient in their facility
        elif user.facility_id and patient.facility_id != user.facility_id:
            return Response(
                {"detail": "Patient must belong to your facility."},
                status=403
            )
        
        # Check if patient has HMO to detach
        if not patient.hmo:
            return Response(
                {"detail": "Patient does not have active HMO coverage."},
                status=400
            )

        patient.hmo = None
        patient.insurance_status = InsuranceStatus.SELF_PAY
        patient.insurance_number = ""
        patient.insurance_expiry = None
        patient.insurance_notes = ""
        
        patient.save(update_fields=[
            "hmo", 
            "insurance_status",
            "insurance_number",
            "insurance_expiry",
            "insurance_notes",
            "updated_at"
        ])
        
        return Response(PatientSerializer(patient, context={"request": request}).data)
    

class IsSystemAdmin(permissions.BasePermission):
    """
    Only allow system-level admins (Django staff or superuser).
    Used for managing the master HMO list.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            (request.user.is_staff or request.user.is_superuser)
        )


class CanManageHMO(permissions.BasePermission):
    """
    Allow facility admins or independent providers to manage HMO relationships.
    """
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        
        # Super admins and facility admins can manage
        if user.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN):
            return True
        
        # Independent providers can manage their own HMO relationships
        if not getattr(user, 'facility_id', None):
            return user.role in (UserRole.DOCTOR, UserRole.NURSE, UserRole.LAB, UserRole.PHARMACY)
        
        return False


# ============================================================================
# SYSTEM HMO VIEWS (Master List)
# ============================================================================

class SystemHMOViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
):
    """
    ViewSet for System HMOs (master list).
    
    - List/Retrieve: Available to all authenticated users
    - Create/Update: System admins only
    - Delete: Not allowed (deactivate instead)
    
    Endpoints:
    - GET    /api/hmo/system/              - List all active system HMOs
    - GET    /api/hmo/system/{id}/         - Get HMO details with tiers
    - POST   /api/hmo/system/              - Create new HMO (admin only)
    - PATCH  /api/hmo/system/{id}/         - Update HMO (admin only)
    - GET    /api/hmo/system/all/          - List all HMOs including inactive (admin only)
    """
    
    queryset = SystemHMO.objects.prefetch_related(
        Prefetch('tiers', queryset=HMOTier.objects.filter(is_active=True).order_by('level'))
    )
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        qs = self.queryset
        
        # By default, only show active HMOs
        if self.action in ('list', 'retrieve'):
            if not self.request.user.is_staff:
                qs = qs.filter(is_active=True)
        
        # Search filter
        search = self.request.query_params.get('s') or self.request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(nhis_number__icontains=search)
            )
        
        return qs.order_by('name')
    
    def get_serializer_class(self):
        if self.action == 'create':
            return SystemHMOCreateSerializer
        if self.action == 'list':
            return SystemHMOListSerializer
        return SystemHMOSerializer
    
    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsAuthenticated(), IsSystemAdmin()]
        return [IsAuthenticated()]
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated, IsSystemAdmin])
    def all(self, request):
        """List all HMOs including inactive ones (admin only)."""
        qs = SystemHMO.objects.prefetch_related('tiers').order_by('name')
        serializer = SystemHMOSerializer(qs, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsSystemAdmin])
    def toggle_active(self, request, pk=None):
        """Toggle HMO active status."""
        system_hmo = self.get_object()
        system_hmo.is_active = not system_hmo.is_active
        system_hmo.save(update_fields=['is_active', 'updated_at'])
        
        return Response({
            'id': system_hmo.id,
            'name': system_hmo.name,
            'is_active': system_hmo.is_active,
        })
    
    @action(detail=True, methods=['get'])
    def tiers(self, request, pk=None):
        """Get all tiers for an HMO."""
        system_hmo = self.get_object()
        tiers = system_hmo.tiers.filter(is_active=True).order_by('level')
        serializer = HMOTierSerializer(tiers, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def dropdown(self, request):
        """
        Get a simple list of HMOs for dropdown selection.
        Returns only id, name, and tiers.
        """
        qs = SystemHMO.objects.filter(is_active=True).prefetch_related(
            Prefetch('tiers', queryset=HMOTier.objects.filter(is_active=True).order_by('level'))
        ).order_by('name')
        
        data = [
            {
                'id': hmo.id,
                'name': hmo.name,
                'tiers': [
                    {'id': t.id, 'name': t.name, 'level': t.level}
                    for t in hmo.tiers.all()
                ]
            }
            for hmo in qs
        ]
        
        return Response(data)


# ============================================================================
# FACILITY HMO VIEWS
# ============================================================================

class FacilityHMOViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
):
    """
    ViewSet for Facility-HMO relationships.
    
    Enables facilities to:
    - Enable/disable HMOs from the system list
    - Track relationship status with HMOs
    - View which HMOs are available for patient enrollment
    
    Endpoints:
    - GET    /api/facilities/hmos/                     - List enabled HMOs
    - POST   /api/facilities/hmos/enable/              - Enable a system HMO
    - DELETE /api/facilities/hmos/{id}/                - Disable an HMO
    - POST   /api/facilities/hmos/{id}/relationship/   - Update relationship status
    - GET    /api/facilities/hmos/available/           - List available system HMOs to enable
    """
    
    serializer_class = FacilityHMOSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsFacilityStaff]
    
    def get_queryset(self):
        user = self.request.user
        facility = getattr(user, 'facility', None)
        
        qs = FacilityHMO.objects.select_related(
            'system_hmo', 'relationship_updated_by'
        ).prefetch_related(
            Prefetch(
                'system_hmo__tiers',
                queryset=HMOTier.objects.filter(is_active=True).order_by('level')
            )
        )
        
        if facility:
            qs = qs.filter(facility=facility)
        else:
            # Independent provider
            qs = qs.filter(owner=user)
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == 'true')
        
        return qs.order_by('system_hmo__name')
    
    def get_permissions(self):
        if self.action in ('enable', 'destroy', 'update_relationship'):
            return [IsAuthenticated(), CanManageHMO()]
        return [IsAuthenticated(), IsFacilityStaff()]
    
    @action(detail=False, methods=['post'], url_path='enable')
    def enable(self, request):
        """
        Enable a System HMO for this facility/provider.
        
        POST body:
        {
            "system_hmo_id": 1,
            "relationship_notes": "Optional notes",
            "contract_start_date": "2024-01-01",
            "contract_end_date": "2024-12-31",
            "contract_reference": "CONTRACT-001"
        }
        """
        serializer = FacilityHMOCreateSerializer(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        facility_hmo = serializer.save()
        
        output = FacilityHMOSerializer(facility_hmo)
        return Response(output.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'], url_path='relationship')
    def update_relationship(self, request, pk=None):
        """
        Update the relationship status with an HMO.
        
        POST body:
        {
            "relationship_status": "EXCELLENT|GOOD|FAIR|POOR|BAD",
            "relationship_notes": "Optional notes"
        }
        """
        facility_hmo = self.get_object()
        
        serializer = FacilityHMOUpdateRelationshipSerializer(
            facility_hmo,
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        
        output = FacilityHMOSerializer(facility_hmo)
        return Response(output.data)
    
    @action(detail=False, methods=['get'], url_path='available')
    def available(self, request):
        """
        List System HMOs that are available to enable (not yet enabled).
        """
        user = request.user
        facility = getattr(user, 'facility', None)
        
        # Get already enabled HMO IDs
        if facility:
            enabled_ids = FacilityHMO.objects.filter(
                facility=facility
            ).values_list('system_hmo_id', flat=True)
        else:
            enabled_ids = FacilityHMO.objects.filter(
                owner=user
            ).values_list('system_hmo_id', flat=True)
        
        # Get available HMOs
        available = SystemHMO.objects.filter(
            is_active=True
        ).exclude(
            id__in=enabled_ids
        ).prefetch_related('tiers').order_by('name')
        
        serializer = SystemHMOListSerializer(available, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'], url_path='toggle-active')
    def toggle_active(self, request, pk=None):
        """
        Toggle the active status of a facility-HMO relationship.
        Used to temporarily disable an HMO without removing the relationship.
        """
        facility_hmo = self.get_object()
        facility_hmo.is_active = not facility_hmo.is_active
        facility_hmo.save(update_fields=['is_active', 'updated_at'])
        
        return Response({
            'id': facility_hmo.id,
            'system_hmo': facility_hmo.system_hmo.name,
            'is_active': facility_hmo.is_active,
        })
    
    def destroy(self, request, *args, **kwargs):
        """
        Remove/disable an HMO relationship.
        
        Note: This doesn't delete the record, just sets is_active=False
        to preserve historical data.
        """
        facility_hmo = self.get_object()
        facility_hmo.is_active = False
        facility_hmo.save(update_fields=['is_active', 'updated_at'])
        
        return Response(status=status.HTTP_204_NO_CONTENT)


# ============================================================================
# PATIENT HMO APPROVAL VIEWS
# ============================================================================

class PatientHMOApprovalViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    """
    ViewSet for managing patient HMO transfer approvals.
    
    When a patient with existing HMO enrollment registers at a new facility,
    an approval request is created. Facility admins review and approve/reject.
    
    Endpoints:
    - GET    /api/patients/hmo-approvals/              - List pending approvals
    - GET    /api/patients/hmo-approvals/{id}/         - Get approval details
    - POST   /api/patients/hmo-approvals/create/       - Create approval request
    - POST   /api/patients/hmo-approvals/{id}/decide/  - Approve or reject
    """
    
    serializer_class = PatientFacilityHMOApprovalSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsFacilityStaff]
    
    def get_queryset(self):
        user = self.request.user
        facility = getattr(user, 'facility', None)
        
        qs = PatientFacilityHMOApproval.objects.select_related(
            'patient',
            'system_hmo',
            'tier',
            'facility',
            'original_facility',
            'decided_by',
        )
        
        if facility:
            qs = qs.filter(facility=facility)
        else:
            # Independent provider
            qs = qs.filter(owner=user)
        
        # Status filter
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        
        return qs.order_by('-requested_at')
    
    @action(detail=False, methods=['get'], url_path='pending')
    def pending(self, request):
        """List only pending approval requests."""
        qs = self.get_queryset().filter(
            status=PatientFacilityHMOApproval.Status.PENDING
        )
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'], url_path='create')
    def create_request(self, request):
        """
        Create a new HMO approval request for a transferring patient.
        
        POST body:
        {
            "patient_id": 123,
            "notes": "Optional notes"
        }
        
        This is typically called automatically when a patient with existing
        HMO registers at a new facility.
        """
        serializer = PatientFacilityHMOApprovalCreateSerializer(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        approval = serializer.save()
        
        output = PatientFacilityHMOApprovalSerializer(approval)
        return Response(output.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'], url_path='decide')
    def decide(self, request, pk=None):
        """
        Approve or reject an HMO transfer request.
        
        POST body:
        {
            "action": "approve" | "reject",
            "notes": "Optional decision notes"
        }
        """
        approval = self.get_object()
        
        serializer = PatientTransferHMOApprovalSerializer(
            data=request.data,
            context={'request': request, 'approval': approval}
        )
        serializer.is_valid(raise_exception=True)
        
        action_type = serializer.validated_data['action']
        notes = serializer.validated_data.get('notes', '')
        
        if action_type == 'approve':
            approval.approve(request.user, notes)
        else:
            approval.reject(request.user, notes)
        
        output = PatientFacilityHMOApprovalSerializer(approval)
        return Response(output.data)
    
    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        """
        Get summary statistics of HMO approvals.
        """
        qs = self.get_queryset()
        
        summary = {
            'pending': qs.filter(status=PatientFacilityHMOApproval.Status.PENDING).count(),
            'approved': qs.filter(status=PatientFacilityHMOApproval.Status.APPROVED).count(),
            'rejected': qs.filter(status=PatientFacilityHMOApproval.Status.REJECTED).count(),
            'total': qs.count(),
        }
        
        return Response(summary)


# ============================================================================
# PATIENT HMO ENROLLMENT ACTIONS
# ============================================================================
# 
# These functions should be added as actions to the existing PatientViewSet
# 

def patient_attach_system_hmo(viewset, request, pk=None):
    """
    Attach a patient to a System HMO with tier selection.
    
    This replaces the old attach_hmo action.
    
    POST /api/patients/{id}/attach-system-hmo/
    
    Body:
    {
        "system_hmo_id": 1,
        "tier_id": 2,
        "insurance_number": "INS-123456",
        "insurance_expiry": "2025-12-31",
        "insurance_notes": "Optional notes"
    }
    """
    from .models import Patient
    from .serializers import PatientSerializer
    
    patient = viewset.get_object()
    user = request.user
    facility = getattr(user, 'facility', None)
    
    # Validate request data
    serializer = PatientAttachHMOSerializer(
        data=request.data,
        context={'request': request}
    )
    serializer.is_valid(raise_exception=True)
    
    system_hmo = serializer.context.get('system_hmo')
    tier = serializer.context.get('tier')
    
    # Check if patient is transferring from another facility with same HMO
    needs_approval = False
    if patient.system_hmo and patient.system_hmo_id == system_hmo.id:
        # Same HMO, check if from different facility
        if patient.hmo_enrollment_facility_id and patient.hmo_enrollment_facility_id != (facility.id if facility else None):
            needs_approval = True
        elif patient.hmo_enrollment_provider_id and patient.hmo_enrollment_provider_id != (user.id if not facility else None):
            needs_approval = True
    
    if needs_approval:
        # Create approval request instead of direct attachment
        approval, created = PatientFacilityHMOApproval.objects.get_or_create(
            patient=patient,
            facility=facility if facility else None,
            owner=user if not facility else None,
            system_hmo=system_hmo,
            defaults={
                'tier': tier,
                'insurance_number': serializer.validated_data.get('insurance_number', patient.insurance_number),
                'insurance_expiry': serializer.validated_data.get('insurance_expiry', patient.insurance_expiry),
                'original_facility': patient.hmo_enrollment_facility,
                'original_provider': patient.hmo_enrollment_provider,
                'status': PatientFacilityHMOApproval.Status.PENDING,
            }
        )
        
        return Response({
            'status': 'approval_required',
            'message': 'Patient has existing HMO enrollment from another facility. Approval request created.',
            'approval_id': approval.id,
        }, status=status.HTTP_202_ACCEPTED)
    
    # Direct enrollment (new HMO or first-time enrollment)
    patient.system_hmo = system_hmo
    patient.hmo_tier = tier
    patient.insurance_status = InsuranceStatus.INSURED
    patient.insurance_number = serializer.validated_data.get('insurance_number', '').strip()
    patient.insurance_expiry = serializer.validated_data.get('insurance_expiry')
    patient.insurance_notes = serializer.validated_data.get('insurance_notes', '').strip()
    patient.hmo_enrollment_facility = facility if facility else None
    patient.hmo_enrollment_provider = user if not facility else None
    patient.hmo_enrolled_at = timezone.now()
    
    patient.save(update_fields=[
        'system_hmo', 'hmo_tier', 'insurance_status',
        'insurance_number', 'insurance_expiry', 'insurance_notes',
        'hmo_enrollment_facility', 'hmo_enrollment_provider', 'hmo_enrolled_at',
        'updated_at',
    ])
    
    return Response(PatientSerializer(patient, context={'request': request}).data)


def patient_clear_system_hmo(viewset, request, pk=None):
    """
    Remove a patient's HMO enrollment (marks as self-pay).
    
    POST /api/patients/{id}/clear-system-hmo/
    """
    from .models import Patient
    from .serializers import PatientSerializer
    
    patient = viewset.get_object()
    
    patient.system_hmo = None
    patient.hmo_tier = None
    patient.insurance_status = InsuranceStatus.SELF_PAY
    patient.insurance_number = ''
    patient.insurance_expiry = None
    patient.insurance_notes = ''
    # Keep enrollment history for audit purposes
    # patient.hmo_enrollment_facility = None
    # patient.hmo_enrollment_provider = None
    # patient.hmo_enrolled_at = None
    
    patient.save(update_fields=[
        'system_hmo', 'hmo_tier', 'insurance_status',
        'insurance_number', 'insurance_expiry', 'insurance_notes',
        'updated_at',
    ])
    
    return Response(PatientSerializer(patient, context={'request': request}).data)


def patient_check_hmo_transfer(viewset, request, pk=None):
    """
    Check if a patient needs HMO transfer approval at this facility.
    
    GET /api/patients/{id}/check-hmo-transfer/
    
    Returns:
    - needs_approval: bool
    - existing_enrollment: HMO details if patient has one
    - approval_status: If approval exists, its status
    """
    patient = viewset.get_object()
    user = request.user
    facility = getattr(user, 'facility', None)
    
    result = {
        'needs_approval': False,
        'existing_enrollment': None,
        'approval_status': None,
        'approval_id': None,
    }
    
    # Check if patient has HMO enrollment
    if not patient.system_hmo:
        return Response(result)
    
    result['existing_enrollment'] = {
        'system_hmo_id': patient.system_hmo_id,
        'system_hmo_name': patient.system_hmo.name,
        'tier_id': patient.hmo_tier_id if patient.hmo_tier else None,
        'tier_name': patient.hmo_tier.name if patient.hmo_tier else None,
        'insurance_number': patient.insurance_number,
        'insurance_expiry': patient.insurance_expiry,
        'enrollment_facility': patient.hmo_enrollment_facility.name if patient.hmo_enrollment_facility else None,
        'enrollment_provider': str(patient.hmo_enrollment_provider) if patient.hmo_enrollment_provider else None,
    }
    
    # Check if enrolled at different facility/provider
    if facility:
        is_different_source = (
            patient.hmo_enrollment_facility_id and 
            patient.hmo_enrollment_facility_id != facility.id
        )
    else:
        is_different_source = (
            patient.hmo_enrollment_provider_id and 
            patient.hmo_enrollment_provider_id != user.id
        )
    
    if is_different_source:
        result['needs_approval'] = True
        
        # Check for existing approval
        approval_q = PatientFacilityHMOApproval.objects.filter(
            patient=patient,
            system_hmo=patient.system_hmo,
        )
        
        if facility:
            approval_q = approval_q.filter(facility=facility)
        else:
            approval_q = approval_q.filter(owner=user)
        
        approval = approval_q.order_by('-requested_at').first()
        
        if approval:
            result['approval_status'] = approval.status
            result['approval_id'] = approval.id
            
            # If already approved, no new approval needed
            if approval.status == PatientFacilityHMOApproval.Status.APPROVED:
                result['needs_approval'] = False
    
    return Response(result)