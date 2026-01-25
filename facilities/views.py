from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Count, Sum, Q, Prefetch

from patients.models import SystemHMO, HMOTier, FacilityHMO, PatientFacilityHMOApproval, Patient
from patients.serializers import (
    SystemHMOSerializer,
    HMOTierSerializer,
    FacilityHMOSerializer,
    FacilityHMOCreateSerializer,
    PatientFacilityHMOApprovalSerializer,
)
from facilities.models import Facility
from .permissions import IsFacilityStaff, IsFacilitySuperAdmin, IsFacilityAdmin
from rest_framework import viewsets, mixins, status, filters
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from django_filters.rest_framework import DjangoFilterBackend
from facilities.permissions_utils import has_facility_permission
from accounts.models import User
from accounts.enums import UserRole
from patients.models import HMO
from .models import (
    Facility,
    FacilityRolePermission,
    Specialty,
    Ward,
    Bed,
    FacilityExtraDocument,
    BedAssignment,
    refresh_bed_status,
)
from .serializers import (
    FacilityCreateSerializer,
    FacilityDetailSerializer,
    SpecialtySerializer,
    WardSerializer,
    BedSerializer,
    FacilityExtraDocumentSerializer,
    FacilityAdminSignupSerializer,
    BedAssignmentSerializer,
    FacilityHMOSerializer as FacilityLegacyHMOSerializer,  # Renamed to avoid conflict
    FacilityRolePermissionSerializer,
)


# ============================================================================
# FACILITY SYSTEM HMO VIEWSET
# ============================================================================

class FacilitySystemHMOViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for listing available SystemHMOs that a facility can enable.
    
    This is read-only - facilities select from the system-wide HMO list.
    Actual HMO management (create/update/delete) is admin-only.
    
    Endpoints:
    - GET /api/facilities/{facility_id}/system-hmos/ - List all active SystemHMOs
    - GET /api/facilities/{facility_id}/system-hmos/{id}/ - Get SystemHMO detail
    - GET /api/facilities/{facility_id}/system-hmos/{id}/tiers/ - Get tiers for an HMO
    """
    
    serializer_class = SystemHMOSerializer
    permission_classes = [IsAuthenticated, IsFacilityStaff]
    authentication_classes = [JWTAuthentication]
    
    def get_queryset(self):
        """Return active SystemHMOs with tier counts."""
        return SystemHMO.objects.filter(
            is_active=True
        ).prefetch_related(
            Prefetch(
                'tiers',
                queryset=HMOTier.objects.filter(is_active=True).order_by('level')
            )
        ).annotate(
            tier_count=Count('tiers', filter=Q(tiers__is_active=True)),
            facility_count=Count('facility_links', filter=Q(facility_links__is_active=True))
        ).order_by('name')
    
    @action(detail=True, methods=['get'])
    def tiers(self, request, pk=None, facility_id=None):
        """Get all tiers for a specific SystemHMO."""
        system_hmo = self.get_object()
        tiers = system_hmo.tiers.filter(is_active=True).order_by('level')
        serializer = HMOTierSerializer(tiers, many=True)
        return Response(serializer.data)


# ============================================================================
# FACILITY HMO MANAGEMENT VIEWSET (Nested under facility)
# ============================================================================

class FacilityHMOManagementViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing a facility's HMO relationships.
    
    This manages the FacilityHMO junction table - which HMOs a facility
    has enabled and their relationship details.
    
    Endpoints:
    - GET /api/facilities/{facility_id}/hmos/ - List facility's enabled HMOs
    - POST /api/facilities/{facility_id}/hmos/ - Enable an HMO for facility
    - GET /api/facilities/{facility_id}/hmos/{id}/ - Get HMO relationship detail
    - PATCH /api/facilities/{facility_id}/hmos/{id}/ - Update relationship
    - DELETE /api/facilities/{facility_id}/hmos/{id}/ - Disable HMO for facility
    """
    
    permission_classes = [IsAuthenticated, IsFacilityStaff]
    authentication_classes = [JWTAuthentication]
    
    def get_serializer_class(self):
        if self.action == 'create':
            return FacilityHMOCreateSerializer
        return FacilityHMOSerializer
    
    def get_queryset(self):
        """Return HMOs enabled for this facility."""
        # Get facility from URL or user
        facility_id = self.kwargs.get('facility_id')
        if not facility_id:
            facility_id = getattr(self.request.user, 'facility_id', None)
        
        if not facility_id:
            return FacilityHMO.objects.none()
        
        return FacilityHMO.objects.filter(
            facility_id=facility_id
        ).select_related(
            'system_hmo',
            'relationship_updated_by'
        ).prefetch_related(
            Prefetch(
                'system_hmo__tiers',
                queryset=HMOTier.objects.filter(is_active=True).order_by('level')
            )
        ).order_by('system_hmo__name')
    
    def get_serializer_context(self):
        context = super().get_serializer_context()
        facility_id = self.kwargs.get('facility_id')
        if not facility_id:
            facility_id = getattr(self.request.user, 'facility_id', None)
        context['facility_id'] = facility_id
        return context
    
    def perform_create(self, serializer):
        """Enable an HMO for the facility."""
        facility_id = self.kwargs.get('facility_id')
        if not facility_id:
            facility_id = getattr(self.request.user, 'facility_id', None)
        
        if not facility_id:
            raise ValueError("No facility ID found")
        
        facility = Facility.objects.get(id=facility_id)
        serializer.save(facility=facility)
    
    def perform_destroy(self, instance):
        """
        Soft-delete: deactivate instead of hard delete.
        This preserves historical data and relationships.
        """
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])
    
    @action(detail=True, methods=['post'])
    def reactivate(self, request, pk=None, facility_id=None):
        """Reactivate a previously disabled HMO relationship."""
        instance = self.get_object()
        instance.is_active = True
        instance.save(update_fields=['is_active', 'updated_at'])
        serializer = self.get_serializer(instance)
        return Response(serializer.data)
    
    def retrieve(self, request, pk=None, facility_pk=None):
        """
        Get HMO relationship details with financial summary.
        
        Query Parameters:
        - patient: Filter charges by patient ID
        - start: Start date for charges (YYYY-MM-DD)
        - end: End date for charges (YYYY-MM-DD)
        - status: Filter by charge status (PAID, UNPAID, PARTIALLY_PAID, VOID)
        
        Returns HMO data with financial summary, charges list, and patients list.
        """
        from billing.models import Charge, Payment
        from billing.serializers import ChargeReadSerializer
        from patients.serializers import PatientSerializer
        from decimal import Decimal
        from django.utils.dateparse import parse_date
        from django.db.models import Sum, Count
        from django.db.models.functions import Coalesce
        
        # Get the HMO relationship
        hmo_relationship = self.get_object()
        
        # Use facility_pk from URL, fallback to user's facility
        if not facility_pk:
            facility_pk = getattr(request.user, 'facility_id', None)
        
        # Get all patients enrolled in this HMO at this facility
        # NOTE: Patient model doesn't have is_active field, just filter by system_hmo
        patients_qs = Patient.objects.filter(
            facility_id=facility_pk,
            system_hmo=hmo_relationship.system_hmo,
        ).select_related('user', 'hmo_tier')
        
        # Get patient IDs for charge filtering
        patient_ids = list(patients_qs.values_list('id', flat=True))
        
        # Get charges for these patients with allocated_total annotation
        # NOTE: allocated_total must be annotated, it's not a model field
        charges_qs = Charge.objects.filter(
            patient_id__in=patient_ids,
            facility_id=facility_pk
        ).select_related(
            'patient',
            'patient__user',
            'patient__system_hmo',
            'patient__hmo_tier',
            'facility',
            'owner',
            'service',
            'created_by'
        ).prefetch_related(
            'allocations'
        ).annotate(
            # This annotation is required by ChargeReadSerializer
            allocated_total=Coalesce(Sum('allocations__amount'), Decimal('0.00'))
        )
        
        # Apply filters from query parameters
        start_date = request.query_params.get('start')
        end_date = request.query_params.get('end')
        status_filter = request.query_params.get('status')
        patient_filter = request.query_params.get('patient')
        
        if start_date:
            try:
                start_parsed = parse_date(start_date)
                if start_parsed:
                    charges_qs = charges_qs.filter(created_at__date__gte=start_parsed)
            except:
                pass
        
        if end_date:
            try:
                end_parsed = parse_date(end_date)
                if end_parsed:
                    charges_qs = charges_qs.filter(created_at__date__lte=end_parsed)
            except:
                pass
        
        if status_filter:
            charges_qs = charges_qs.filter(status=status_filter)
        
        if patient_filter:
            try:
                patient_id = int(patient_filter)
                charges_qs = charges_qs.filter(patient_id=patient_id)
                # Also filter patients list
                patients_qs = patients_qs.filter(id=patient_id)
            except (ValueError, TypeError):
                pass
        
        # Calculate summary statistics
        # The queryset already has allocated_total annotated
        charge_aggregates = charges_qs.aggregate(
            total_amount=Sum('amount'),
            total_allocated=Sum('allocated_total'),
            count=Count('id')
        )
        
        total_charges = charge_aggregates['total_amount'] or Decimal('0.00')
        total_allocated = charge_aggregates['total_allocated'] or Decimal('0.00')
        outstanding = total_charges - total_allocated
        charges_count = charge_aggregates['count'] or 0
        # Get total payments (allocated) for this HMO
        # Payments are tracked via allocations; bulk HMO payments may have no patient set.
        from billing.models import PaymentAllocation

        payment_total = PaymentAllocation.objects.filter(
            charge_id__in=charges_qs.values('id'),
            payment__payment_source='HMO',
        ).aggregate(
            total=Coalesce(Sum('amount'), Decimal('0.00'))
        )['total'] or Decimal('0.00')
        
        # Serialize charges
        # ChargeReadSerializer expects allocated_total to be present (we annotated it)
        charges_data = ChargeReadSerializer(charges_qs, many=True).data
        
        # Serialize patients
        patients_data = PatientSerializer(patients_qs, many=True).data
        
        # Get the HMO serializer data
        hmo_serializer = self.get_serializer(hmo_relationship)
        hmo_data = hmo_serializer.data
        
        # Add SystemHMO contact fields to the response
        # These fields come from the related SystemHMO
        if hmo_relationship.system_hmo:
            hmo_data['name'] = hmo_relationship.system_hmo.name
        
        # Build complete response
        response_data = {
            'hmo': hmo_data,
            'summary': {
                'total_patients': patients_qs.count(),
                'charges_total': float(total_charges),
                'charges_count': charges_count,
                'payments_total': float(payment_total),
                'paid_total': float(total_allocated),
                'outstanding': float(outstanding),
            },
            'charges': charges_data,
            'patients': patients_data,
        }
        
        return Response(response_data)

    @action(detail=True, methods=['post'])
    def update_relationship(self, request, pk=None, facility_pk=None):
        """
        Update the relationship status and notes.
        
        Body:
        {
            "status": "GOOD",     # Maps to relationship_status
            "notes": "Updated..."  # Maps to relationship_notes
        }
        """
        instance = self.get_object()
        
        # Frontend sends 'status' and 'notes', map to model fields
        status_value = request.data.get('status')
        notes_value = request.data.get('notes')
        
        # Also support the old field names for backward compatibility
        if not status_value:
            status_value = request.data.get('relationship_status')
        if notes_value is None:
            notes_value = request.data.get('relationship_notes')
        
        if status_value:
            # Validate status
            valid_statuses = ['EXCELLENT', 'GOOD', 'FAIR', 'POOR', 'BAD']
            if status_value not in valid_statuses:
                return Response(
                    {'detail': f'Invalid status. Must be one of: {", ".join(valid_statuses)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            instance.relationship_status = status_value
        
        if notes_value is not None:
            instance.relationship_notes = notes_value
        
        instance.relationship_updated_by = request.user
        instance.relationship_updated_at = timezone.now()
        instance.save()
        
        serializer = self.get_serializer(instance)
        return Response({
            'status': 'success',
            'message': 'Relationship status updated successfully',
            'data': serializer.data
        })
    
    @action(detail=False, methods=['get'])
    def available(self, request, facility_id=None):
        """
        List SystemHMOs that are NOT yet enabled for this facility.
        Useful for the "Add HMO" dropdown.
        """
        if not facility_id:
            facility_id = getattr(request.user, 'facility_id', None)
        
        if not facility_id:
            return Response([])
        
        enabled_hmo_ids = FacilityHMO.objects.filter(
            facility_id=facility_id,
            is_active=True
        ).values_list('system_hmo_id', flat=True)
        
        available_hmos = SystemHMO.objects.filter(
            is_active=True
        ).exclude(
            id__in=enabled_hmo_ids
        ).prefetch_related(
            'tiers'
        ).order_by('name')
        
        serializer = SystemHMOSerializer(available_hmos, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def patients(self, request, pk=None, facility_id=None):
        """Get patients enrolled with this HMO at this facility."""
        instance = self.get_object()
        
        if not facility_id:
            facility_id = getattr(request.user, 'facility_id', None)
        
        patients = Patient.objects.filter(
            facility_id=facility_id,
            system_hmo=instance.system_hmo,
        ).select_related(
            'hmo_tier'
        ).order_by('last_name', 'first_name')
        
        # Return simplified patient list
        data = [
            {
                'id': p.id,
                'first_name': p.first_name,
                'last_name': p.last_name,
                'insurance_number': p.insurance_number,
                'tier': p.hmo_tier.name if p.hmo_tier else None,
            }
            for p in patients
        ]
        
        return Response(data)
    
    @action(detail=True, methods=['get'])
    def pricing(self, request, pk=None, facility_id=None):
        """Get HMO pricing configured for this facility."""
        from billing.models import HMOPrice
        
        instance = self.get_object()
        
        if not facility_id:
            facility_id = getattr(request.user, 'facility_id', None)
        
        prices = HMOPrice.objects.filter(
            facility_id=facility_id,
            system_hmo=instance.system_hmo,
            is_active=True
        ).select_related(
            'service',
            'tier'
        ).order_by('service__name', 'tier__level')
        
        # Format for response
        pricing_data = []
        for price in prices:
            pricing_data.append({
                'id': price.id,
                'service_id': price.service_id,
                'service_name': price.service.name,
                'tier_id': price.tier_id,
                'tier_name': price.tier.name if price.tier else 'All Tiers',
                'amount': str(price.amount),
                'is_active': price.is_active,
            })
        
        return Response(pricing_data)


# ============================================================================
# FACILITY HMO APPROVAL VIEWSET
# ============================================================================

class FacilityHMOApprovalViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing HMO transfer approvals at a facility.
    
    When a patient with an existing HMO enrollment visits a new facility,
    they may need approval before their HMO coverage applies.
    
    Endpoints:
    - GET /api/facilities/{facility_id}/hmo-approvals/ - List pending approvals
    - GET /api/facilities/{facility_id}/hmo-approvals/{id}/ - Get approval detail
    - POST /api/facilities/{facility_id}/hmo-approvals/{id}/approve/ - Approve
    - POST /api/facilities/{facility_id}/hmo-approvals/{id}/reject/ - Reject
    """
    
    serializer_class = PatientFacilityHMOApprovalSerializer
    permission_classes = [IsAuthenticated, IsFacilityStaff]
    authentication_classes = [JWTAuthentication]
    
    def get_queryset(self):
        facility_id = self.kwargs.get('facility_id')
        if not facility_id:
            facility_id = getattr(self.request.user, 'facility_id', None)
        
        if not facility_id:
            return PatientFacilityHMOApproval.objects.none()
        
        queryset = PatientFacilityHMOApproval.objects.filter(
            facility_id=facility_id
        ).select_related(
            'patient',
            'system_hmo',
            'tier',
            'decided_by',
            'original_facility',
            'original_provider'
        ).order_by('-requested_at')
        
        # Filter by status if provided
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())
        
        return queryset
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None, facility_id=None):
        """Approve the HMO transfer request."""
        instance = self.get_object()
        
        if instance.status != 'PENDING':
            return Response(
                {'detail': 'This request has already been processed'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        decision_notes = request.data.get('decision_notes', '')
        
        instance.status = 'APPROVED'
        instance.decided_by = request.user
        instance.decided_at = timezone.now()
        instance.decision_notes = decision_notes
        instance.save()
        
        # Update patient's HMO enrollment to this facility
        if not facility_id:
            facility_id = getattr(request.user, 'facility_id', None)
        
        patient = instance.patient
        patient.hmo_enrollment_facility_id = facility_id
        patient.hmo_enrollment_provider = None
        patient.hmo_enrolled_at = timezone.now()
        patient.save(update_fields=[
            'hmo_enrollment_facility', 
            'hmo_enrollment_provider',
            'hmo_enrolled_at',
            'updated_at'
        ])
        
        serializer = self.get_serializer(instance)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None, facility_id=None):
        """Reject the HMO transfer request."""
        instance = self.get_object()
        
        if instance.status != 'PENDING':
            return Response(
                {'detail': 'This request has already been processed'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        decision_notes = request.data.get('decision_notes', '')
        if not decision_notes:
            return Response(
                {'decision_notes': 'Please provide a reason for rejection'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        instance.status = 'REJECTED'
        instance.decided_by = request.user
        instance.decided_at = timezone.now()
        instance.decision_notes = decision_notes
        instance.save()
        
        serializer = self.get_serializer(instance)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def pending_count(self, request, facility_id=None):
        """Get count of pending approvals for dashboard."""
        if not facility_id:
            facility_id = getattr(request.user, 'facility_id', None)
        
        if not facility_id:
            return Response({'pending_count': 0})
        
        count = PatientFacilityHMOApproval.objects.filter(
            facility_id=facility_id,
            status='PENDING'
        ).count()
        
        return Response({'pending_count': count})


# ============================================================================
# FACILITY VIEWSET
# ============================================================================

class FacilityViewSet(
    viewsets.GenericViewSet,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
):
    queryset = Facility.objects.filter(is_active=True)
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["state", "facility_type"]
    search_fields = ["name", "address", "state"]
    ordering_fields = ["name", "created_at"]

    def get_queryset(self):
        '''
        Filter facilities based on user role and visibility settings.
        Patients only see publicly visible facilities.
        '''
        queryset = super().get_queryset()
        user = self.request.user
        
        # Check if user is a patient (not staff or provider)
        is_patient = (
            hasattr(user, 'patient_profile') and 
            getattr(user, 'role', None) == UserRole.PATIENT
        )
        
        # Patients only see publicly visible facilities
        if is_patient:
            queryset = queryset.filter(is_publicly_visible=True)
        
        # Optional: Add query parameter override for explicit visibility filtering
        visibility_param = self.request.query_params.get('is_publicly_visible', None)
        if visibility_param is not None:
            if visibility_param.lower() in ['true', '1', 'yes']:
                queryset = queryset.filter(is_publicly_visible=True)
            elif visibility_param.lower() in ['false', '0', 'no']:
                queryset = queryset.filter(is_publicly_visible=False)
        
        return queryset

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return FacilityCreateSerializer
        return FacilityDetailSerializer

    @transaction.atomic
    def perform_create(self, serializer):
        """
        Facility Super Admin registration flow:
        - Create facility
        - If creator has no facility, link them & elevate to SUPER_ADMIN
        """
        facility = serializer.save()
        user = self.request.user
        if not user.facility:
            user.facility = facility
        # If a patient or no role, promote to SUPER_ADMIN (Hospital SuperAdmin)
        if user.role in (UserRole.PATIENT,) or not user.role:
            user.role = UserRole.SUPER_ADMIN
        user.save()

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, IsFacilityAdmin],
    )
    def upload_extra(self, request, pk=None):
        facility = self.get_object()
        s = FacilityExtraDocumentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.save(facility=facility)
        return Response(s.data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, IsFacilityAdmin],
    )
    def add_ward(self, request, pk=None):
        facility = self.get_object()
        s = WardSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.save(facility=facility)
        return Response(s.data, status=201)

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, IsFacilityAdmin],
    )
    def add_bed(self, request, pk=None):
        """
        Add one bed or multiple (if payload contains list under 'items').
        """
        facility = self.get_object()
        ward_id = request.data.get("ward")
        if not ward_id:
            return Response({"detail": "ward is required"}, status=400)
        try:
            ward = facility.wards.get(id=ward_id)
        except Ward.DoesNotExist:
            return Response(
                {"detail": "Ward not found for this facility"}, status=404
            )

        items = request.data.get("items")
        if items and isinstance(items, list):
            created = []
            for item in items:
                s = BedSerializer(data={"ward": ward.id, **item})
                s.is_valid(raise_exception=True)
                s.save()
                created.append(s.data)
            return Response({"created": created}, status=201)

        s = BedSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data, status=201)

    @action(
        detail=True,
        methods=["get"],
        url_path="ward-summary",
        permission_classes=[IsAuthenticated],
    )
    def ward_summary(self, request, pk=None):
        """
        Returns a compact summary of wards and bed usage for this facility.
        """
        facility = self.get_object()
        wards = facility.wards.prefetch_related("beds").order_by("name")

        summary = []
        for w in wards:
            beds = list(w.beds.all())
            bed_count = len(beds)

            occupied = sum(
                1 for b in beds if b.status == Bed.BedStatus.OCCUPIED
            )
            available = sum(
                1
                for b in beds
                if b.status == Bed.BedStatus.AVAILABLE and b.is_operational
            )

            summary.append(
                {
                    "id": w.id,
                    "name": w.name,
                    "ward_type": w.ward_type,
                    "ward_type_display": w.get_ward_type_display(),
                    "gender_policy": w.gender_policy,
                    "gender_policy_display": w.get_gender_policy_display(),
                    "floor": w.floor,
                    "capacity": w.capacity,
                    "bed_count": bed_count,
                    "occupied_beds": occupied,
                    "available_beds": available,
                }
            )

        return Response(summary)

    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
    def specialties(self, request):
        qs = Specialty.objects.all().order_by("name")
        return Response(SpecialtySerializer(qs, many=True).data)

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[IsAuthenticated, IsFacilityAdmin],
    )
    def seed_specialties(self, request):
        """
        Seed your provided specialty list from the doc (idempotent).
        """
        names = request.data.get("names") or []
        created = []
        for n in names:
            obj, was_created = Specialty.objects.get_or_create(name=n.strip())
            if was_created:
                created.append(obj.name)
        return Response({"created": created}, status=201)

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, IsFacilityAdmin],
    )
    def assign_user(self, request, pk=None):
        """
        Assign an existing user to this facility and set role.
        payload: { "user_id": "...", "role": "DOCTOR" }
        """
        facility = self.get_object()
        user_id = request.data.get("user_id")
        role = request.data.get("role")
        if not (user_id and role):
            return Response(
                {"detail": "user_id and role are required"}, status=400
            )
        try:
            u = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found"}, status=404)
        u.facility = facility
        u.role = role
        u.save()
        return Response({"ok": True})
    
    # =========================================================================
    # PERMISSIONS ACTIONS
    # =========================================================================
    
    @action(
        detail=False,
        methods=['get'],
        url_path="permissions",
        permission_classes=[IsAuthenticated, IsFacilitySuperAdmin],
    )
    def permissions(self, request):
        """
        GET /api/facilities/permissions/
        
        List all permission configurations for the current user's facility.
        Super Admin only.
        """
        user = request.user
        
        if not user.facility:
            return Response(
                {'error': 'User not associated with a facility'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        perms = FacilityRolePermission.objects.filter(
            facility=user.facility
        ).order_by('role')
        
        result = []
        for perm in perms:
            perm_fields = {
                field.name: getattr(perm, field.name)
                for field in FacilityRolePermission._meta.fields
                if field.name.startswith('can_')
            }
            
            for permission_name, enabled in perm_fields.items():
                result.append({
                    'id': f"{perm.id}-{permission_name}",
                    'facility': perm.facility.id,
                    'role': perm.role,
                    'permission': permission_name,
                    'enabled': enabled,
                    'created_at': perm.created_at.isoformat() if hasattr(perm, 'created_at') else None,
                    'updated_at': perm.updated_at.isoformat() if hasattr(perm, 'updated_at') else None,
                })
        
        return Response(result)

    @action(
        detail=False,
        methods=['post'],
        url_path="permissions/bulk_update",
        permission_classes=[IsAuthenticated, IsFacilitySuperAdmin],
    )
    def bulk_update_permissions(self, request):
        """
        POST /api/facilities/permissions/bulk_update/
        
        Bulk update permissions for one or more roles.
        """
        user = request.user
        
        if not user.facility:
            return Response(
                {'error': 'User not associated with a facility'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        permissions_data = request.data.get('permissions', [])
        
        if not isinstance(permissions_data, list):
            return Response(
                {'error': 'permissions must be a list'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        updates_by_role = {}
        for perm_data in permissions_data:
            role = perm_data.get('role')
            permission = perm_data.get('permission')
            enabled = perm_data.get('enabled', True)
            
            if not role or not permission:
                continue
            
            if role not in updates_by_role:
                updates_by_role[role] = {}
            updates_by_role[role][permission] = enabled
        
        updated_count = 0
        for role, perms_dict in updates_by_role.items():
            perm_obj, created = FacilityRolePermission.objects.get_or_create(
                facility=user.facility,
                role=role,
                defaults={'updated_by': user}
            )
            
            for permission_name, enabled_value in perms_dict.items():
                if hasattr(perm_obj, permission_name):
                    setattr(perm_obj, permission_name, enabled_value)
                    updated_count += 1
            
            perm_obj.updated_by = user
            perm_obj.save()
        
        return Response({
            'message': f'Updated {updated_count} permissions',
            'count': updated_count,
            'roles_affected': list(updates_by_role.keys())
        })

    @action(
        detail=False,
        methods=['post'],
        url_path="permissions/reset_role",
        permission_classes=[IsAuthenticated, IsFacilitySuperAdmin],
    )
    def reset_role_permissions(self, request):
        """
        POST /api/facilities/permissions/reset_role/
        
        Reset a role's permissions to defaults (delete custom config).
        """
        user = request.user
        
        if not user.facility:
            return Response(
                {'error': 'User not associated with a facility'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        role = request.data.get('role')
        
        if not role:
            return Response(
                {'error': 'role is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        deleted_count, _ = FacilityRolePermission.objects.filter(
            facility=user.facility,
            role=role
        ).delete()
        
        return Response({
            'message': f'Reset {role} to default permissions',
            'deleted': deleted_count,
            'role': role
        })

    @action(
        detail=False,
        methods=['get'],
        url_path="my-permissions",
        permission_classes=[IsAuthenticated],
    )
    def my_permissions(self, request):
        """
        GET /api/facilities/my-permissions/
        
        Get the current user's effective permissions in their facility.
        """
        from facilities.permissions_utils import get_user_permissions
        
        user = request.user
        permissions = get_user_permissions(user)
        
        has_custom = False
        if user.facility_id and user.role != UserRole.SUPER_ADMIN:
            has_custom = FacilityRolePermission.objects.filter(
                facility_id=user.facility_id,
                role=user.role
            ).exists()
        
        return Response({
            'role': user.role,
            'role_display': user.get_role_display() if hasattr(user, 'get_role_display') else user.role,
            'facility_id': user.facility_id,
            'permissions': permissions,
            'is_super_admin': user.role == UserRole.SUPER_ADMIN,
            'has_custom_permissions': has_custom,
        })


# ============================================================================
# BED ASSIGNMENT VIEWSET
# ============================================================================

class BedAssignmentViewSet(viewsets.ModelViewSet):
    """
    Manage bed assignments (bed ← patient) within a facility.
    """
    serializer_class = BedAssignmentSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = BedAssignment.objects.select_related(
            "bed",
            "bed__ward",
            "bed__ward__facility",
            "patient",
            "encounter",
        )

        user = self.request.user
        if getattr(user, "facility_id", None):
            qs = qs.filter(bed__ward__facility_id=user.facility_id)

        bed_id = self.request.query_params.get("bed")
        ward_id = self.request.query_params.get("ward")
        patient_id = self.request.query_params.get("patient")
        active = self.request.query_params.get("active")

        if bed_id:
            qs = qs.filter(bed_id=bed_id)
        if ward_id:
            qs = qs.filter(bed__ward_id=ward_id)
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if active in ("true", "True", "1"):
            qs = qs.filter(discharged_at__isnull=True)

        return qs.order_by("-assigned_at")

    def perform_create(self, serializer):
        serializer.save()

    @action(detail=True, methods=["post"])
    def discharge(self, request, pk=None):
        if not has_facility_permission(request.user, 'can_discharge_patients'):
            return Response(
                {"detail": "You do not have permission to discharge patients."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        assignment = self.get_object()
        if assignment.discharged_at is not None:
            return Response(
                {"detail": "Assignment is already discharged."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        assignment.discharged_at = timezone.now()
        user = request.user
        if user and user.is_authenticated:
            assignment.discharged_by = user
        assignment.save()

        serializer = self.get_serializer(assignment)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        """
        Move an active bed assignment to a different bed (within same facility).
        """
        assignment = self.get_object()
        if assignment.discharged_at is not None:
            return Response(
                {"detail": "Cannot transfer a discharged assignment."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_bed_id = request.data.get("bed")
        try:
            new_bed_id = int(new_bed_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Valid 'bed' id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            new_bed = Bed.objects.select_related(
                "ward", "ward__facility"
            ).get(pk=new_bed_id)
        except Bed.DoesNotExist:
            return Response(
                {"detail": "New bed not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        user = request.user
        if getattr(user, "facility_id", None):
            if new_bed.ward.facility_id != user.facility_id:
                return Response(
                    {"detail": "You can only transfer within your facility."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        if new_bed.assignments.filter(discharged_at__isnull=True).exists():
            return Response(
                {"detail": "Target bed already has an active assignment."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_bed = assignment.bed
        assignment.bed = new_bed
        assignment.save()

        refresh_bed_status(old_bed)
        refresh_bed_status(new_bed)

        serializer = self.get_serializer(assignment)
        return Response(serializer.data)


# ============================================================================
# FACILITY ADMIN REGISTER VIEW (PUBLIC)
# ============================================================================

class FacilityAdminRegisterView(APIView):
    """
    Public endpoint to create Facility + Super Admin and return tokens.
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, *args, **kwargs):
        s = FacilityAdminSignupSerializer(
            data=request.data, context={"request": request}
        )
        s.is_valid(raise_exception=True)
        payload = s.save()
        return Response(payload, status=status.HTTP_201_CREATED)


# ============================================================================
# LEGACY HMO VIEWSET (For backward compatibility)
# ============================================================================

class FacilityHMOViewSet(viewsets.ModelViewSet):
    """
    Legacy HMO ViewSet - manages facility-scoped HMOs.
    
    This is kept for backward compatibility with existing code.
    New implementations should use the System HMO endpoints.
    """
    
    serializer_class = FacilityLegacyHMOSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsFacilityStaff]
    
    def get_queryset(self):
        user = self.request.user
        facility_id = getattr(user, 'facility_id', None)
        
        if not facility_id:
            return HMO.objects.none()
        
        return HMO.objects.filter(
            facility_id=facility_id
        ).order_by('name')
    
    def perform_create(self, serializer):
        user = self.request.user
        serializer.save(facility=user.facility)