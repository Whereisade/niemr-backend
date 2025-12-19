# accounts/facility_admin_views.py
"""
Views for facility admin/staff management.
Only SUPER_ADMIN can create/manage ADMIN and FRONTDESK users.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.shortcuts import get_object_or_404

from .models import User
from .enums import UserRole
from .facility_admin_serializers import (
    FacilityStaffCreateSerializer,
    FacilityStaffListSerializer,
    FacilityStaffUpdateSerializer,
)


class IsSuperAdmin:
    """Permission check for SUPER_ADMIN role."""
    def has_permission(self, request, view=None):
        return bool(
            request.user 
            and request.user.is_authenticated 
            and request.user.role == UserRole.SUPER_ADMIN
        )


def check_super_admin(request):
    """Helper to check if user is SUPER_ADMIN and has a facility."""
    if request.user.role != UserRole.SUPER_ADMIN:
        return Response(
            {"detail": "Only Super Admins can manage facility staff."},
            status=status.HTTP_403_FORBIDDEN,
        )
    if not getattr(request.user, "facility", None):
        return Response(
            {"detail": "You must be attached to a facility."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def list_facility_staff(request):
    """
    List all ADMIN and FRONTDESK staff for the current facility.
    SUPER_ADMIN only.
    
    Query params:
    - role: Filter by role (ADMIN, FRONTDESK)
    - is_active: Filter by active status (true/false)
    - q: Search by name or email
    """
    error = check_super_admin(request)
    if error:
        return error

    facility = request.user.facility

    # Get staff users (ADMIN, FRONTDESK) for this facility
    qs = User.objects.filter(
        facility=facility,
        role__in=[UserRole.ADMIN, UserRole.FRONTDESK],
    ).order_by("-date_joined")

    # Apply filters
    role = request.query_params.get("role")
    if role:
        qs = qs.filter(role=role.upper())

    is_active = request.query_params.get("is_active")
    if is_active is not None:
        qs = qs.filter(is_active=is_active.lower() in ("true", "1", "yes"))

    q = request.query_params.get("q")
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(first_name__icontains=q) |
            Q(last_name__icontains=q) |
            Q(email__icontains=q)
        )

    serializer = FacilityStaffListSerializer(qs, many=True)
    return Response(serializer.data)


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def create_facility_staff(request):
    """
    Create a new ADMIN or FRONTDESK user for the current facility.
    SUPER_ADMIN only.
    
    POST body:
    {
        "email": "admin@example.com",
        "password": "securepassword123",
        "first_name": "Jane",
        "last_name": "Doe",
        "role": "ADMIN",  // or "FRONTDESK"
        "phone": "+2348012345678"  // optional
    }
    """
    error = check_super_admin(request)
    if error:
        return error

    serializer = FacilityStaffCreateSerializer(
        data=request.data,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    result = serializer.save()

    return Response(result, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def get_facility_staff(request, pk):
    """
    Get details of a specific staff member.
    SUPER_ADMIN only.
    """
    error = check_super_admin(request)
    if error:
        return error

    facility = request.user.facility
    user = get_object_or_404(
        User,
        pk=pk,
        facility=facility,
        role__in=[UserRole.ADMIN, UserRole.FRONTDESK],
    )

    serializer = FacilityStaffListSerializer(user)
    return Response(serializer.data)


@api_view(["PATCH"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def update_facility_staff(request, pk):
    """
    Update a staff member's details.
    SUPER_ADMIN only.
    
    PATCH body (all optional):
    {
        "first_name": "Jane",
        "last_name": "Smith",
        "is_active": true
    }
    """
    error = check_super_admin(request)
    if error:
        return error

    facility = request.user.facility
    user = get_object_or_404(
        User,
        pk=pk,
        facility=facility,
        role__in=[UserRole.ADMIN, UserRole.FRONTDESK],
    )

    serializer = FacilityStaffUpdateSerializer(
        user,
        data=request.data,
        partial=True,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()

    return Response(FacilityStaffListSerializer(user).data)


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def deactivate_facility_staff(request, pk):
    """
    Deactivate a staff member (soft delete).
    SUPER_ADMIN only.
    """
    error = check_super_admin(request)
    if error:
        return error

    facility = request.user.facility
    user = get_object_or_404(
        User,
        pk=pk,
        facility=facility,
        role__in=[UserRole.ADMIN, UserRole.FRONTDESK],
    )

    # Prevent deactivating yourself
    if user.id == request.user.id:
        return Response(
            {"detail": "You cannot deactivate your own account."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user.is_active = False
    user.save(update_fields=["is_active"])

    return Response({"detail": "Staff member deactivated.", "id": user.id})


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def reactivate_facility_staff(request, pk):
    """
    Reactivate a deactivated staff member.
    SUPER_ADMIN only.
    """
    error = check_super_admin(request)
    if error:
        return error

    facility = request.user.facility
    user = get_object_or_404(
        User,
        pk=pk,
        facility=facility,
        role__in=[UserRole.ADMIN, UserRole.FRONTDESK],
    )

    user.is_active = True
    user.save(update_fields=["is_active"])

    return Response({"detail": "Staff member reactivated.", "id": user.id})