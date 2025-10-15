from rest_framework.permissions import BasePermission
from accounts.enums import UserRole

class IsFacilityAdmin(BasePermission):
    """
    SUPER_ADMIN or ADMIN of a facility.
    """
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN))
