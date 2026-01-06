from django.db import transaction
from django.utils import timezone

from rest_framework import viewsets, mixins, status
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

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
    FacilityHMOSerializer,
    FacilityRolePermissionSerializer,
)
from .permissions import IsFacilityAdmin, IsFacilityStaff, IsFacilitySuperAdmin



class FacilityHMOViewSet(viewsets.ModelViewSet):
    """
    Facility-scoped HMO management.

    - Any facility staff can list/view HMOs for their facility.
    - Only facility SUPER_ADMIN can create/update/delete HMOs.
    """
    serializer_class = FacilityHMOSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsFacilityStaff]

    def get_queryset(self):
        facility_id = getattr(self.request.user, "facility_id", None)
        if not facility_id:
            return HMO.objects.none()
        return HMO.objects.filter(facility_id=facility_id).order_by("name")

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAuthenticated(), IsFacilitySuperAdmin()]
        return [IsAuthenticated(), IsFacilityStaff()]

    def perform_create(self, serializer):
        serializer.save(facility=self.request.user.facility)



class FacilityViewSet(
    viewsets.GenericViewSet,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
):
    queryset = Facility.objects.all().order_by("-created_at")
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

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

            # Using the new status field
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
    
    @action(
        detail=True,
        methods=["get", "post"],
        url_path="role-permissions",
        permission_classes=[IsAuthenticated, IsFacilitySuperAdmin],
    )
    def role_permissions(self, request, pk=None):
        """
        GET: List all role permissions for this facility
        POST: Create permissions for a specific role
        
        POST body: {
            "role": "PHARMACY",
            "can_manage_pharmacy_catalog": false,
            "can_manage_pharmacy_stock": false,
            ...
        }
        """
        facility = self.get_object()
        
        if request.method == "GET":
            perms = FacilityRolePermission.objects.filter(facility=facility).order_by('role')
            serializer = FacilityRolePermissionSerializer(perms, many=True)
            
            # Also return which roles don't have permissions configured yet
            configured_roles = set(perms.values_list('role', flat=True))
            available_roles = [
                UserRole.DOCTOR,
                UserRole.NURSE,
                UserRole.LAB,
                UserRole.PHARMACY,
                UserRole.FRONTDESK,
                UserRole.ADMIN,
            ]
            unconfigured_roles = [r for r in available_roles if r not in configured_roles]
            
            return Response({
                "permissions": serializer.data,
                "unconfigured_roles": unconfigured_roles,
            })
        
        # POST - create new role permission
        role = request.data.get('role')
        if not role:
            return Response({"detail": "role is required"}, status=400)
        
        # Check if already exists
        if FacilityRolePermission.objects.filter(facility=facility, role=role).exists():
            return Response(
                {"detail": f"Permissions for {role} already exist. Use PATCH to update."},
                status=400
            )
        
        serializer = FacilityRolePermissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(facility=facility, updated_by=request.user)
        
        return Response(serializer.data, status=201)

    @action(
        detail=True,
        methods=["get", "patch"],
        url_path="role-permissions/(?P<role>[^/.]+)",
        permission_classes=[IsAuthenticated, IsFacilitySuperAdmin],
    )
    def role_permission_detail(self, request, pk=None, role=None):
        """
        GET: Get permissions for a specific role
        PATCH: Update permissions for a specific role
        
        PATCH body: {
            "can_manage_pharmacy_catalog": false,
            "can_manage_pharmacy_stock": true,
            ...
        }
        """
        facility = self.get_object()
        
        try:
            perm = FacilityRolePermission.objects.get(facility=facility, role=role)
        except FacilityRolePermission.DoesNotExist:
            # Create default permissions if they don't exist
            if request.method == "GET":
                # Return default (all enabled)
                from facilities.permissions_utils import _get_all_permissions
                return Response({
                    "role": role,
                    "permissions": _get_all_permissions(enabled=True),
                    "is_default": True,
                    "message": "No custom permissions configured. All permissions enabled by default."
                })
            else:
                # Create new
                perm = FacilityRolePermission.objects.create(
                    facility=facility,
                    role=role,
                    updated_by=request.user
                )
        
        if request.method == "GET":
            serializer = FacilityRolePermissionSerializer(perm)
            return Response(serializer.data)
        
        # PATCH
        serializer = FacilityRolePermissionSerializer(perm, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        
        return Response(serializer.data)
    
    @action(
        detail=True,
        methods=["delete"],
        url_path="role-permissions/(?P<role>[^/.]+)/reset",
        permission_classes=[IsAuthenticated, IsFacilitySuperAdmin],
    )
    def reset_role_permissions(self, request, pk=None, role=None):
        """
        DELETE: Reset permissions for a role to defaults (delete custom config)
        """
        facility = self.get_object()
        
        try:
            perm = FacilityRolePermission.objects.get(facility=facility, role=role)
            perm.delete()
            return Response({
                "detail": f"Permissions reset to default for {role}",
                "role": role
            })
        except FacilityRolePermission.DoesNotExist:
            return Response({
                "detail": f"No custom permissions found for {role}. Already using defaults.",
                "role": role
            })

    @action(
        detail=False,
        methods=["get"],
        url_path="my-permissions",
        permission_classes=[IsAuthenticated],
    )
    def my_permissions(self, request):
        """
        Get the current user's effective permissions in their facility.
        Useful for frontend to know what UI elements to show/hide.
        """
        from facilities.permissions_utils import get_user_permissions
        
        user = request.user
        permissions = get_user_permissions(user)
        
        # Check if custom permissions exist
        has_custom = False
        if user.facility_id and user.role != UserRole.SUPER_ADMIN:
            has_custom = FacilityRolePermission.objects.filter(
                facility_id=user.facility_id,
                role=user.role
            ).exists()
        
        return Response({
            "role": user.role,
            "role_display": user.get_role_display() if hasattr(user, 'get_role_display') else user.role,
            "facility_id": user.facility_id,
            "permissions": permissions,
            "is_super_admin": user.role == UserRole.SUPER_ADMIN,
            "has_custom_permissions": has_custom,
        })


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
        """
        Mark this bed assignment as discharged (free the bed).
        """
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
        Body: {"bed": <new_bed_id>}
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
        # Facility scoping
        if getattr(user, "facility_id", None):
            if new_bed.ward.facility_id != user.facility_id:
                return Response(
                    {"detail": "You can only transfer within your facility."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Ensure no active assignment on new bed
        if new_bed.assignments.filter(discharged_at__isnull=True).exists():
            return Response(
                {"detail": "Target bed already has an active assignment."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_bed = assignment.bed
        assignment.bed = new_bed
        assignment.save()

        # Refresh both beds
        refresh_bed_status(old_bed)
        refresh_bed_status(new_bed)

        serializer = self.get_serializer(assignment)
        return Response(serializer.data)


# --- NIEMR: Public endpoint to create Facility + Super Admin and return tokens ---
class FacilityAdminRegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, *args, **kwargs):
        s = FacilityAdminSignupSerializer(
            data=request.data, context={"request": request}
        )
        s.is_valid(raise_exception=True)
        payload = s.save()  # dict with facility, user, tokens
        return Response(payload, status=status.HTTP_201_CREATED)


