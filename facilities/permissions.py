from rest_framework.permissions import BasePermission
from accounts.enums import UserRole

class IsFacilityAdmin(BasePermission):
    """
    SUPER_ADMIN or ADMIN of a facility.
    """
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN))


class IsFacilityStaff(BasePermission):
    def has_permission(self, request, view):
        u = getattr(request, 'user', None)
        if not u or not u.is_authenticated:
            return False
        if not getattr(u, 'facility_id', None):
            return False
        return u.role in (
            UserRole.SUPER_ADMIN,
            UserRole.ADMIN,
            UserRole.DOCTOR,
            UserRole.NURSE,
            UserRole.LAB,
            UserRole.PHARMACY,
            UserRole.FRONTDESK,
        )


class IsFacilitySuperAdmin(BasePermission):
    def has_permission(self, request, view):
        u = getattr(request, 'user', None)
        return bool(u and u.is_authenticated and getattr(u, 'facility_id', None) and u.role == UserRole.SUPER_ADMIN)
