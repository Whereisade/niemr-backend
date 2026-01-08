from django.db import transaction
from django.utils import timezone

from rest_framework import viewsets, mixins, status
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
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
        # 🆕 Allow admins to update relationship status
        if self.action == "update_relationship_status":
            return [IsAuthenticated(), IsFacilityAdmin()]
        return [IsAuthenticated(), IsFacilityStaff()]

    def perform_create(self, serializer):
        serializer.save(facility=self.request.user.facility)
    
    @action(
        detail=True,
        methods=["post"],
        url_path="relationship-status",
        permission_classes=[IsAuthenticated, IsFacilityAdmin]
    )
    def update_relationship_status(self, request, pk=None):
        """
        POST /api/facilities/hmos/{id}/relationship-status/
        
        Update the relationship status for an HMO.
        Only SUPER_ADMIN and ADMIN can update this.
        
        Body: {
            "status": "EXCELLENT" | "GOOD" | "FAIR" | "POOR" | "BAD",
            "notes": "Optional notes about the status"
        }
        """
        hmo = self.get_object()
        
        status = request.data.get("status")
        notes = request.data.get("notes", "")
        
        # Validate status
        valid_statuses = [choice[0] for choice in HMO.RelationshipStatus.choices]
        if not status or status not in valid_statuses:
            return Response(
                {
                    "detail": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
                },
                status=400
            )
        
        # Update relationship status
        hmo.relationship_status = status
        hmo.relationship_notes = notes.strip()
        hmo.relationship_updated_at = timezone.now()
        hmo.relationship_updated_by = request.user
        hmo.save(update_fields=[
            "relationship_status",
            "relationship_notes",
            "relationship_updated_at",
            "relationship_updated_by",
            "updated_at"
        ])
        
        serializer = self.get_serializer(hmo)
        return Response(serializer.data)



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
    detail=False,  # ✅ Changed from True
    methods=['get'],
    url_path="permissions",  # ✅ Changed from "role-permissions"
    permission_classes=[IsAuthenticated, IsFacilitySuperAdmin],
    )
    def permissions(self, request):
        """
        GET /api/facilities/permissions/
        
        List all permission configurations for the current user's facility.
        Super Admin only.
        
        Returns array of permission objects.
        """
        user = request.user
        
        if not user.facility:
            return Response(
                {'error': 'User not associated with a facility'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get all permissions for this facility
        perms = FacilityRolePermission.objects.filter(
            facility=user.facility
        ).order_by('role')
        
        # Serialize to match frontend expectations
        result = []
        for perm in perms:
            # Get all permission fields from the model
            perm_fields = {
                field.name: getattr(perm, field.name)
                for field in FacilityRolePermission._meta.fields
                if field.name.startswith('can_')
            }
            
            # Add each permission field as a separate object
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
        detail=False,  # ✅ Changed from True
        methods=['post'],
        url_path="permissions/bulk_update",  # ✅ New action
        permission_classes=[IsAuthenticated, IsFacilitySuperAdmin],
    )
    def bulk_update_permissions(self, request):
            """
            POST /api/facilities/permissions/bulk_update/
            
            Bulk update permissions for one or more roles.
            
            Body: {
                "permissions": [
                    {"role": "DOCTOR", "permission": "can_view_patients", "enabled": true},
                    {"role": "DOCTOR", "permission": "can_edit_patients", "enabled": false},
                    ...
                ]
            }
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
            
            # Group permissions by role
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
            
            # Update or create permissions for each role
            updated_count = 0
            for role, perms_dict in updates_by_role.items():
                # Get or create the permission object for this role
                perm_obj, created = FacilityRolePermission.objects.get_or_create(
                    facility=user.facility,
                    role=role,
                    defaults={'updated_by': user}
                )
                
                # Update each permission field
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
        detail=False,  # ✅ Changed from True
        methods=['post'],
        url_path="permissions/reset_role",  # ✅ Changed URL pattern
        permission_classes=[IsAuthenticated, IsFacilitySuperAdmin],
    )
    def reset_role_permissions(self, request):
        """
        POST /api/facilities/permissions/reset_role/
        
        Reset a role's permissions to defaults (delete custom config).
        
        Body: {"role": "DOCTOR"}
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
        
        # Delete all custom permissions for this role
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
        All authenticated users can call this.
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
            'role': user.role,
            'role_display': user.get_role_display() if hasattr(user, 'get_role_display') else user.role,
            'facility_id': user.facility_id,
            'permissions': permissions,
            'is_super_admin': user.role == UserRole.SUPER_ADMIN,
            'has_custom_permissions': has_custom,
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
        if not has_facility_permission(request.user, 'can_discharge_patients'):
            return Response(
                {"detail": "You do not have permission to discharge patients."},
                status=status.HTTP_403_FORBIDDEN
            )
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


