from django.db.models import Q, Prefetch, Count, Sum, F
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.shortcuts import get_object_or_404
from decimal import Decimal
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


# ============================================================================
# PATIENT VIEWSET
# ============================================================================

class PatientViewSet(viewsets.GenericViewSet,
                     mixins.CreateModelMixin,
                     mixins.RetrieveModelMixin,
                     mixins.UpdateModelMixin,
                     mixins.ListModelMixin):
    queryset = Patient.objects.select_related(
        "user", "facility", "hmo", "system_hmo", "hmo_tier", 'hmo_enrollment_facility'
    ).all().order_by("-created_at")
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create",):
            return PatientCreateByStaffSerializer
        return PatientSerializer

    def get_permissions(self):
        if self.action == "create":
            return [IsAuthenticated(), IsStaff()]
        elif self.action == "list":
            return [IsAuthenticated()]
        elif self.action in ("retrieve", "update", "partial_update"):
            return [IsAuthenticated(), IsSelfOrFacilityStaff()]
        return super().get_permissions()

    def list(self, request, *args, **kwargs):
        q = self.queryset
        u = request.user

        # PATIENT role users see only their own record
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

        # Basic search
        s = request.query_params.get("s")
        if s:
            q = q.filter(
                Q(first_name__icontains=s) | Q(last_name__icontains=s) |
                Q(email__icontains=s) | Q(phone__icontains=s)
            )
        
        page = self.paginate_queryset(q)
        if page is not None:
            ser = PatientSerializer(page, many=True, context={"request": request})
            return self.get_paginated_response(ser.data)
        ser = PatientSerializer(q, many=True, context={"request": request})
        return Response(ser.data)

    def perform_create(self, serializer):
        """On independent provider create, auto-link patient to the creator."""
        patient = serializer.save()
        u = self.request.user
        
        # For independent provider staff (no facility), link the created patient
        if not getattr(u, "facility_id", None):
            role = (getattr(u, "role", "") or "").upper()
            if role not in {"SUPER_ADMIN", "ADMIN"} and role in {
                "DOCTOR", "NURSE", "LAB", "PHARMACY", "FRONTDESK",
            }:
                PatientProviderLink.objects.get_or_create(patient=patient, provider=u)
        return patient



    # =========================================================================
    # PATIENT DASHBOARD SUMMARY
    # =========================================================================

    @action(detail=False, methods=["get"], url_path="dashboard-summary", permission_classes=[IsAuthenticated])
    def dashboard_summary(self, request):
        """Return patient dashboard summary metrics (patient + dependents).

        Includes:
          - total_visits (encounters count)
          - billing outstanding balance + unpaid charges count
          - quick health metrics (pending labs, active prescriptions, pending imaging)
          - latest vitals snapshot
        """
        u = request.user
        role = (getattr(u, "role", "") or "").upper()
        if role != UserRole.PATIENT:
            return Response({"detail": "Only patients can access this endpoint."}, status=403)

        base_patient = getattr(u, "patient_profile", None)
        if not base_patient:
            return Response({"detail": "Patient profile not found."}, status=404)

        # Patient scope: self + dependents
        patient_ids = list(
            Patient.objects.filter(Q(id=base_patient.id) | Q(parent_patient=base_patient))
            .values_list("id", flat=True)
        )

        # Local imports to avoid circular dependencies
        from encounters.models import Encounter
        from appointments.models import Appointment
        from billing.models import Charge
        from billing.enums import ChargeStatus
        from labs.models import LabOrder
        from labs.enums import OrderStatus
        from pharmacy.models import Prescription
        from pharmacy.enums import RxStatus
        from imaging.models import ImagingRequest
        from imaging.enums import RequestStatus
        from vitals.models import VitalSign

        total_visits = Encounter.objects.filter(patient_id__in=patient_ids).count()
        total_appointments = Appointment.objects.filter(patient_id__in=patient_ids).count()

        # Billing outstanding (PATIENT LIABILITY ONLY)
        #
        # If a patient is insured (HMO), those outstanding amounts should not be
        # shown on the patient dashboard as "your" outstanding bills.
        # We therefore only include self-pay charges here.
        charges_qs = (
            Charge.objects.filter(patient_id__in=patient_ids)
            .filter(
                patient__insurance_status="SELF_PAY",
                patient__system_hmo__isnull=True,
                patient__hmo__isnull=True,
            )
            .exclude(status=ChargeStatus.VOID)
            .annotate(allocated_total=Coalesce(Sum("allocations__amount"), Decimal("0.00")))
            .annotate(outstanding=F("amount") - F("allocated_total"))
        )

        billing_agg = charges_qs.aggregate(
            outstanding_balance=Coalesce(Sum("outstanding"), Decimal("0.00")),
            unpaid_charges_count=Count(
                "id",
                filter=Q(status__in=[ChargeStatus.UNPAID, ChargeStatus.PARTIALLY_PAID]),
            ),
        )

        outstanding_balance = billing_agg.get("outstanding_balance") or Decimal("0.00")
        # Guard against negative values due to over-allocation
        if outstanding_balance < 0:
            outstanding_balance = Decimal("0.00")

        unpaid_charges_count = int(billing_agg.get("unpaid_charges_count") or 0)

        pending_labs = LabOrder.objects.filter(
            patient_id__in=patient_ids,
            status__in=[OrderStatus.PENDING, OrderStatus.IN_PROGRESS],
        ).count()

        active_prescriptions = Prescription.objects.filter(
            patient_id__in=patient_ids,
            status__in=[RxStatus.PRESCRIBED, RxStatus.PARTIALLY_DISPENSED],
        ).count()

        pending_imaging = ImagingRequest.objects.filter(
            patient_id__in=patient_ids,
            status__in=[RequestStatus.REQUESTED, RequestStatus.SCHEDULED],
        ).count()

        latest = (
            VitalSign.objects.filter(patient_id__in=patient_ids)
            .order_by("-measured_at", "-id")
            .first()
        )

        latest_vitals = None
        if latest:
            latest_vitals = {
                "id": latest.id,
                "patient_id": latest.patient_id,
                "measured_at": latest.measured_at,
                "systolic": latest.systolic,
                "diastolic": latest.diastolic,
                "heart_rate": latest.heart_rate,
                "temp_c": str(latest.temp_c) if latest.temp_c is not None else None,
                "resp_rate": latest.resp_rate,
                "spo2": latest.spo2,
                "weight_kg": str(latest.weight_kg) if latest.weight_kg is not None else None,
                "height_cm": str(latest.height_cm) if latest.height_cm is not None else None,
                "bmi": str(latest.bmi) if latest.bmi is not None else None,
                "overall": latest.overall,
            }

        return Response(
            {
                "patient_id": base_patient.id,
                "scoped_patient_ids": patient_ids,
                "total_visits": total_visits,
                "total_appointments": total_appointments,
                "billing": {
                    "outstanding_balance": outstanding_balance,
                    "unpaid_charges_count": unpaid_charges_count,
                },
                "metrics": {
                    "pending_labs": pending_labs,
                    "active_prescriptions": active_prescriptions,
                    "pending_imaging": pending_imaging,
                },
                "latest_vitals": latest_vitals,
            }
        )

    # =========================================================================
    # DOCUMENT ACTIONS
    # =========================================================================
    
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

    # =========================================================================
    # LEGACY HMO ACTIONS (Facility-scoped HMOs)
    # =========================================================================
    
    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated, IsStaff])
    def hmos(self, request):
        """List active HMOs for the requester's facility (legacy)."""
        facility_id = getattr(request.user, "facility_id", None)
        if not facility_id:
            return Response([])
        qs = HMO.objects.filter(facility_id=facility_id, is_active=True).order_by("name")
        return Response(HMOSerializer(qs, many=True).data)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def seed_hmos(self, request):
        """Bulk-create HMOs for the requester's facility (SUPER_ADMIN only, legacy)."""
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
        """Attach a patient to a facility-scoped HMO (legacy)."""
        patient = self.get_object()

        user_facility_id = getattr(request.user, "facility_id", None)
        if not user_facility_id or patient.facility_id != user_facility_id:
            return Response({"detail": "Patient must belong to your facility."}, status=403)

        hmo_id = request.data.get("hmo_id")
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
            "hmo", "insurance_status", "insurance_number",
            "insurance_expiry", "insurance_notes", "updated_at"
        ])
        
        return Response(PatientSerializer(patient, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="clear-hmo", permission_classes=[IsAuthenticated, IsStaff])
    def clear_hmo(self, request, pk=None):
        """Remove a patient's HMO attachment (legacy, marks as self-pay)."""
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
            "hmo", "insurance_status", "insurance_number",
            "insurance_expiry", "insurance_notes", "updated_at"
        ])
        
        return Response(PatientSerializer(patient, context={"request": request}).data)

    # =========================================================================
    # SYSTEM HMO ACTIONS (New system-scoped HMOs with tiers)
    # =========================================================================
    
    @action(
        detail=True,
        methods=['post'],
        url_path='attach-system-hmo',
        permission_classes=[IsAuthenticated, IsStaff]
    )
    def attach_system_hmo(self, request, pk=None):
        """
        Attach a patient to a System HMO with tier selection.
        
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
        patient = self.get_object()
        user = request.user
        facility = getattr(user, 'facility', None)
        
        # Validate request data
        serializer = PatientAttachHMOSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        system_hmo_id = serializer.validated_data.get('system_hmo_id')
        tier_id = serializer.validated_data.get('tier_id')
        
        # Get validated objects
        try:
            system_hmo = SystemHMO.objects.get(id=system_hmo_id, is_active=True)
        except SystemHMO.DoesNotExist:
            return Response(
                {"system_hmo_id": "HMO not found or is inactive"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            tier = HMOTier.objects.get(
                id=tier_id,
                system_hmo=system_hmo,
                is_active=True
            )
        except HMOTier.DoesNotExist:
            return Response(
                {"tier_id": "Tier not found or does not belong to this HMO"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if facility/provider has enabled this HMO
        hmo_enabled = FacilityHMO.objects.filter(
            system_hmo=system_hmo,
            is_active=True
        )
        if facility:
            hmo_enabled = hmo_enabled.filter(facility=facility)
        else:
            hmo_enabled = hmo_enabled.filter(owner=user)
        
        if not hmo_enabled.exists():
            return Response(
                {"system_hmo_id": "This HMO is not enabled for your facility/practice. Please enable it first."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if patient is transferring from another facility with same HMO
        needs_approval = False
        if patient.system_hmo and patient.system_hmo_id == system_hmo.id:
            # Same HMO, check if from different facility
            if patient.hmo_enrollment_facility_id:
                if facility and patient.hmo_enrollment_facility_id != facility.id:
                    needs_approval = True
                elif not facility and patient.hmo_enrollment_facility_id:
                    needs_approval = True
            elif patient.hmo_enrollment_provider_id:
                if facility:
                    needs_approval = True
                elif not facility and patient.hmo_enrollment_provider_id != user.id:
                    needs_approval = True
        
        if needs_approval:
            # Create approval request instead of direct attachment
            approval, created = PatientFacilityHMOApproval.objects.get_or_create(
                patient=patient,
                facility=facility if facility else None,
                owner=user if not facility else None,
                system_hmo=system_hmo,
                status=PatientFacilityHMOApproval.Status.PENDING,
                defaults={
                    'tier': tier,
                    'insurance_number': serializer.validated_data.get('insurance_number', '') or patient.insurance_number,
                    'insurance_expiry': serializer.validated_data.get('insurance_expiry') or patient.insurance_expiry,
                    'original_facility': patient.hmo_enrollment_facility,
                    'original_provider': patient.hmo_enrollment_provider,
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
        patient.insurance_number = (serializer.validated_data.get('insurance_number') or '').strip()
        patient.insurance_expiry = serializer.validated_data.get('insurance_expiry')
        patient.insurance_notes = (serializer.validated_data.get('insurance_notes') or '').strip()
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

    @action(
        detail=True,
        methods=['post'],
        url_path='clear-system-hmo',
        permission_classes=[IsAuthenticated, IsStaff]
    )
    def clear_system_hmo(self, request, pk=None):
        """
        Remove a patient's System HMO enrollment (marks as self-pay).
        
        POST /api/patients/{id}/clear-system-hmo/
        """
        patient = self.get_object()
        user = request.user
        facility = getattr(user, 'facility', None)
        
        # Check permission
        if facility:
            if patient.facility_id != facility.id:
                return Response(
                    {"detail": "Patient must belong to your facility."},
                    status=status.HTTP_403_FORBIDDEN
                )
        
        patient.system_hmo = None
        patient.hmo_tier = None
        patient.insurance_status = InsuranceStatus.SELF_PAY
        patient.insurance_number = ''
        patient.insurance_expiry = None
        patient.insurance_notes = ''
        # Keep enrollment history for audit purposes
        
        patient.save(update_fields=[
            'system_hmo', 'hmo_tier', 'insurance_status',
            'insurance_number', 'insurance_expiry', 'insurance_notes',
            'updated_at',
        ])
        
        return Response(PatientSerializer(patient, context={'request': request}).data)

    @action(
        detail=True,
        methods=['get'],
        url_path='check-hmo-transfer',
        permission_classes=[IsAuthenticated, IsStaff]
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
        patient = self.get_object()
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
            ) or (
                patient.hmo_enrollment_provider_id is not None
            )
        else:
            is_different_source = (
                patient.hmo_enrollment_provider_id and 
                patient.hmo_enrollment_provider_id != user.id
            ) or (
                patient.hmo_enrollment_facility_id is not None
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

    @action(
        detail=True,
        methods=["post"],
        url_path="detach-hmo",
        permission_classes=[IsAuthenticated]
    )
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
        
        # Check if patient has HMO to detach (check both legacy and system HMO)
        if not patient.hmo and not patient.system_hmo:
            return Response(
                {"detail": "Patient does not have active HMO coverage."},
                status=400
            )

        # Clear both legacy and system HMO
        patient.hmo = None
        patient.system_hmo = None
        patient.hmo_tier = None
        patient.insurance_status = InsuranceStatus.SELF_PAY
        patient.insurance_number = ""
        patient.insurance_expiry = None
        patient.insurance_notes = ""
        
        patient.save(update_fields=[
            "hmo", "system_hmo", "hmo_tier",
            "insurance_status", "insurance_number",
            "insurance_expiry", "insurance_notes",
            "updated_at"
        ])
        
        return Response(PatientSerializer(patient, context={"request": request}).data)

    @action(
        detail=True,
        methods=['get'],
        url_path='hmo-info',
        permission_classes=[IsAuthenticated]
    )
    def hmo_info(self, request, pk=None):
        """
        Get detailed HMO information for a patient.
        
        GET /api/patients/{id}/hmo-info/
        """
        patient = self.get_object()
        
        # Build response
        data = {
            'has_legacy_hmo': patient.hmo_id is not None,
            'has_system_hmo': patient.system_hmo_id is not None,
            'insurance_status': patient.insurance_status,
            'insurance_number': patient.insurance_number,
            'insurance_expiry': patient.insurance_expiry,
            'insurance_notes': patient.insurance_notes,
        }
        
        # Legacy HMO info
        if patient.hmo:
            data['legacy_hmo'] = {
                'id': patient.hmo.id,
                'name': patient.hmo.name,
                'facility_id': patient.hmo.facility_id,
            }
        
        # System HMO info
        if patient.system_hmo:
            data['system_hmo'] = {
                'id': patient.system_hmo.id,
                'name': patient.system_hmo.name,
                'nhis_number': patient.system_hmo.nhis_number,
            }
            
            if patient.hmo_tier:
                data['tier'] = {
                    'id': patient.hmo_tier.id,
                    'name': patient.hmo_tier.name,
                    'level': patient.hmo_tier.level,
                }
            
            # Enrollment info
            data['enrollment'] = {
                'enrolled_at': patient.hmo_enrolled_at,
                'enrollment_type': 'facility' if patient.hmo_enrollment_facility else 'provider' if patient.hmo_enrollment_provider else None,
            }
            
            if patient.hmo_enrollment_facility:
                data['enrollment']['facility_id'] = patient.hmo_enrollment_facility_id
                data['enrollment']['facility_name'] = patient.hmo_enrollment_facility.name
            elif patient.hmo_enrollment_provider:
                data['enrollment']['provider_id'] = patient.hmo_enrollment_provider_id
                data['enrollment']['provider_name'] = f"{patient.hmo_enrollment_provider.first_name} {patient.hmo_enrollment_provider.last_name}".strip()
        
        return Response(data)


# ============================================================================
# SELF REGISTRATION
# ============================================================================

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


# ============================================================================
# DEPENDENT VIEWSET
# ============================================================================

class DependentViewSet(viewsets.ModelViewSet):
    """
    CRUD for dependents (Patients with parent_patient set).
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

        # Patient/guardian → only their own dependents
        patient_profile = getattr(user, "patient_profile", None)
        if patient_profile:
            return qs.filter(parent_patient_id=patient_profile.id)

        return qs.none()

    def perform_create(self, serializer):
        """
        Attach the correct parent_patient based on who is logged in.
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
                raise ValidationError({
                    "parent_patient_id": [
                        "This field is required when staff create a dependent."
                    ]
                })
            parent_patient = get_object_or_404(Patient, pk=parent_id)
        else:
            # Patient/guardian path → attach to their own patient profile
            patient_profile = getattr(user, "patient_profile", None)
            if not patient_profile:
                raise ValidationError({
                    "detail": "You do not have a patient profile to attach dependents to."
                })
            parent_patient = patient_profile

        guardian_user = user if getattr(user, "patient_profile", None) else None

        facility = getattr(user, "facility", None)
        if facility is None and getattr(parent_patient, "facility_id", None):
            facility = parent_patient.facility

        serializer.save(
            parent_patient=parent_patient,
            guardian_user=guardian_user,
            facility=facility,
        )


# Patch the existing PatientViewSet to add nested dependents action
def add_dependents_actions_to_patient_viewset(viewset_cls):
    """
    Dynamically attach nested dependents actions to PatientViewSet.
    """

    @action(detail=True, methods=["get"], url_path="dependents")
    def dependents(self, request, pk=None):
        qs = (
            Patient.objects.select_related("parent_patient")
            .filter(parent_patient_id=pk)
            .order_by("-id")
        )
        serializer = DependentSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)

    @dependents.mapping.post
    def add_dependent(self, request, pk=None):
        parent_patient = get_object_or_404(Patient, pk=pk)

        create_serializer = DependentCreateSerializer(data=request.data)
        create_serializer.is_valid(raise_exception=True)

        user = request.user
        guardian_user = user if getattr(user, "patient_profile", None) else None
        facility = getattr(parent_patient, "facility", None)

        dependent = create_serializer.save(
            parent_patient=parent_patient,
            guardian_user=guardian_user,
            facility=facility,
        )

        detail = DependentSerializer(dependent, context={"request": request})
        return Response(detail.data, status=status.HTTP_201_CREATED)

    setattr(viewset_cls, "dependents", dependents)
    setattr(viewset_cls, "add_dependent", add_dependent)

    return viewset_cls

PatientViewSet = add_dependents_actions_to_patient_viewset(PatientViewSet)


# ============================================================================
# PATIENT DOCUMENT VIEWSET
# ============================================================================

class PatientDocumentViewSet(viewsets.ModelViewSet):
    """
    Patient-attached documents (lab results, imaging, prescriptions, etc.).
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

        return qs.none()

    def create(self, request, *args, **kwargs):
        user = request.user
        patient = None
        
        if getattr(user, "role", None) == UserRole.PATIENT:
            patient = getattr(user, "patient_profile", None)
            if patient is None:
                return Response(
                    {"detail": "No patient profile linked to this user."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
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
        
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        if getattr(user, "role", None) == UserRole.PATIENT:
            uploaded_by_role = PatientDocument.UploadedBy.PATIENT
        else:
            uploaded_by_role = self._guess_uploaded_by_role(user)
        
        document = serializer.save(
            patient=patient,
            uploaded_by=user,
            uploaded_by_role=uploaded_by_role,
        )
        
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def _guess_uploaded_by_role(self, user):
        role = (getattr(user, "role", "") or "").upper()
        if role in PatientDocument.UploadedBy.values:
            return role
        return PatientDocument.UploadedBy.SYSTEM


# ============================================================================
# ALLERGY VIEWSET
# ============================================================================

class AllergyViewSet(viewsets.ModelViewSet):
    """
    CRUD for patient allergies.
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
        user = request.user
        patient = None
        
        if getattr(user, "role", None) == UserRole.PATIENT:
            patient = getattr(user, "patient_profile", None)
            if patient is None:
                return Response(
                    {"detail": "No patient profile linked to this user."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
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
        
        allergy = serializer.save(patient=patient, recorded_by=user)
        
        output_serializer = AllergySerializer(allergy)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)
    
    def update(self, request, *args, **kwargs):
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


# ============================================================================
# PERMISSION CLASSES
# ============================================================================

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
# SYSTEM HMO VIEWSET (Master List)
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
    - GET    /api/patients/hmo/system/              - List all active system HMOs
    - GET    /api/patients/hmo/system/{id}/         - Get HMO details with tiers
    - POST   /api/patients/hmo/system/              - Create new HMO (admin only)
    - PATCH  /api/patients/hmo/system/{id}/         - Update HMO (admin only)
    - GET    /api/patients/hmo/system/all/          - List all HMOs including inactive (admin only)
    - GET    /api/patients/hmo/system/dropdown/     - Simple list for dropdowns
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
# FACILITY HMO VIEWSET
# ============================================================================

class FacilityHMOViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """
    ViewSet for Facility-HMO relationships.
    
    Enables facilities to:
    - Enable/disable HMOs from the system list
    - Track relationship status with HMOs
    - View which HMOs are available for patient enrollment
    
    Endpoints:
    - GET    /api/patients/hmo/facility/                     - List enabled HMOs
    - POST   /api/patients/hmo/facility/enable/              - Enable a system HMO
    - DELETE /api/patients/hmo/facility/{id}/                - Disable an HMO
    - POST   /api/patients/hmo/facility/{id}/relationship/   - Update relationship status
    - GET    /api/patients/hmo/facility/available/           - List available system HMOs to enable
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

        # Filter by active status (optional)
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            v = str(is_active).strip().lower()
            if v in ('true', '1', 'yes'):
                qs = qs.filter(is_active=True)
            elif v in ('false', '0', 'no'):
                qs = qs.filter(is_active=False)
            # else: ignore unknown values (return all)

        return qs.order_by('system_hmo__name')


    def partial_update(self, request, *args, **kwargs):
        """
        PATCH /api/patients/hmo/facility/{id}/

        Supports updating only is_active:
        { "is_active": true|false }
        """
        facility_hmo = self.get_object()

        if 'is_active' not in request.data:
            return Response(
                {"is_active": "This field is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        raw = request.data.get('is_active')
        if isinstance(raw, bool):
            is_active = raw
        else:
            v = str(raw).strip().lower()
            if v in ('true', '1', 'yes'):
                is_active = True
            elif v in ('false', '0', 'no'):
                is_active = False
            else:
                return Response(
                    {"is_active": "Invalid value. Use true/false."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        facility_hmo.is_active = is_active
        facility_hmo.save(update_fields=['is_active', 'updated_at'])
        return Response(FacilityHMOSerializer(facility_hmo).data)
    
    def get_permissions(self):
        # Mutating actions require elevated permission
        if self.action in (
            'enable',
            'destroy',
            'update',
            'partial_update',
            'toggle_active',
            'update_relationship',
        ):
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
        user = request.user
        facility = getattr(user, 'facility', None)
        
        # Validate system_hmo_id
        system_hmo_id = request.data.get('system_hmo_id')
        if not system_hmo_id:
            return Response(
                {"system_hmo_id": "This field is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            system_hmo = SystemHMO.objects.get(id=system_hmo_id, is_active=True)
        except SystemHMO.DoesNotExist:
            return Response(
                {"system_hmo_id": "HMO not found or is inactive."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if already enabled
        existing_q = FacilityHMO.objects.filter(system_hmo=system_hmo)
        if facility:
            existing_q = existing_q.filter(facility=facility)
        else:
            existing_q = existing_q.filter(owner=user)
        
        existing = existing_q.first()
        
        if existing:
            if existing.is_active:
                return Response(
                    {"system_hmo_id": "This HMO is already enabled for your facility/practice."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            else:
                # Reactivate
                existing.is_active = True
                existing.relationship_notes = request.data.get('relationship_notes', existing.relationship_notes)
                existing.contract_start_date = request.data.get('contract_start_date', existing.contract_start_date)
                existing.contract_end_date = request.data.get('contract_end_date', existing.contract_end_date)
                existing.contract_reference = request.data.get('contract_reference', existing.contract_reference)
                # Contact Information
                existing.email = request.data.get('email', existing.email)
                existing.addresses = request.data.get('addresses', existing.addresses)
                existing.contact_numbers = request.data.get('contact_numbers', existing.contact_numbers)
                existing.contact_person_name = request.data.get('contact_person_name', existing.contact_person_name)
                existing.contact_person_phone = request.data.get('contact_person_phone', existing.contact_person_phone)
                existing.contact_person_email = request.data.get('contact_person_email', existing.contact_person_email)
                existing.nhis_number = request.data.get('nhis_number', existing.nhis_number)
                existing.save()
                
                output = FacilityHMOSerializer(existing)
                return Response(output.data, status=status.HTTP_200_OK)
        
        # Create new relationship
        facility_hmo = FacilityHMO.objects.create(
            facility=facility if facility else None,
            owner=user if not facility else None,
            system_hmo=system_hmo,
            relationship_status=request.data.get('relationship_status', FacilityHMO.RelationshipStatus.GOOD),
            relationship_notes=request.data.get('relationship_notes', ''),
            contract_start_date=request.data.get('contract_start_date'),
            contract_end_date=request.data.get('contract_end_date'),
            contract_reference=request.data.get('contract_reference', ''),
            # Contact Information
            email=request.data.get('email', ''),
            addresses=request.data.get('addresses', []),
            contact_numbers=request.data.get('contact_numbers', []),
            contact_person_name=request.data.get('contact_person_name', ''),
            contact_person_phone=request.data.get('contact_person_phone', ''),
            contact_person_email=request.data.get('contact_person_email', ''),
            nhis_number=request.data.get('nhis_number', ''),
            is_active=True,
        )
        
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
        
        relationship_status = request.data.get('relationship_status')
        if relationship_status and relationship_status in dict(FacilityHMO.RelationshipStatus.choices):
            facility_hmo.relationship_status = relationship_status
        
        relationship_notes = request.data.get('relationship_notes')
        if relationship_notes is not None:
            facility_hmo.relationship_notes = relationship_notes
        
        facility_hmo.relationship_updated_at = timezone.now()
        facility_hmo.relationship_updated_by = request.user
        facility_hmo.save()
        
        output = FacilityHMOSerializer(facility_hmo)
        return Response(output.data)
    
    @action(detail=False, methods=['get'], url_path='available')
    def available(self, request):
        """
        List System HMOs that are available to enable (not yet enabled).
        """
        user = request.user
        facility = getattr(user, 'facility', None)
        
        # Get already enabled HMO IDs (both active and inactive, to avoid duplicates)
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
# PATIENT HMO APPROVAL VIEWSET
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
        """
        user = request.user
        facility = getattr(user, 'facility', None)
        
        patient_id = request.data.get('patient_id')
        if not patient_id:
            return Response(
                {"patient_id": "This field is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            patient = Patient.objects.select_related(
                'system_hmo', 'hmo_tier',
                'hmo_enrollment_facility', 'hmo_enrollment_provider'
            ).get(id=patient_id)
        except Patient.DoesNotExist:
            return Response(
                {"patient_id": "Patient not found."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not patient.system_hmo:
            return Response(
                {"patient_id": "Patient does not have an HMO enrollment to transfer."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check for existing pending approval
        existing_q = PatientFacilityHMOApproval.objects.filter(
            patient=patient,
            status=PatientFacilityHMOApproval.Status.PENDING,
        )
        
        if facility:
            existing_q = existing_q.filter(facility=facility)
        else:
            existing_q = existing_q.filter(owner=user)
        
        if existing_q.exists():
            return Response(
                {"patient_id": "There is already a pending HMO approval request for this patient."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create the approval request
        approval = PatientFacilityHMOApproval.objects.create(
            patient=patient,
            facility=facility if facility else None,
            owner=user if not facility else None,
            system_hmo=patient.system_hmo,
            tier=patient.hmo_tier,
            insurance_number=patient.insurance_number,
            insurance_expiry=patient.insurance_expiry,
            original_facility=patient.hmo_enrollment_facility,
            original_provider=patient.hmo_enrollment_provider,
            status=PatientFacilityHMOApproval.Status.PENDING,
            request_notes=request.data.get('notes', ''),
        )
        
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
        
        if approval.status != PatientFacilityHMOApproval.Status.PENDING:
            return Response(
                {"detail": "This request has already been processed."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        action_type = request.data.get('action')
        if action_type not in ('approve', 'reject'):
            return Response(
                {"action": "Must be 'approve' or 'reject'."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        notes = request.data.get('notes', '')
        
        if action_type == 'approve':
            approval.approve(request.user, notes)
            
            # Update patient's enrollment to this facility
            patient = approval.patient
            facility = approval.facility
            
            if facility:
                patient.hmo_enrollment_facility = facility
                patient.hmo_enrollment_provider = None
            else:
                patient.hmo_enrollment_facility = None
                patient.hmo_enrollment_provider = approval.owner
            
            patient.hmo_enrolled_at = timezone.now()
            patient.save(update_fields=[
                'hmo_enrollment_facility',
                'hmo_enrollment_provider',
                'hmo_enrolled_at',
                'updated_at',
            ])
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