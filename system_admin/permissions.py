from rest_framework.permissions import BasePermission
from accounts.enums import UserRole

class IsAppSuperAdmin(BasePermission):
    """
    Application / Platform Super Admin:
      - Django superuser/staff, OR
      - role == SUPER_ADMIN AND facility is NULL (app-level)
    """
    def has_permission(self, request, view):
        u = getattr(request, "user", None)
        if not u or not getattr(u, "is_authenticated", False):
            return False
        if getattr(u, "is_superuser", False) or getattr(u, "is_staff", False):
            return True
        return getattr(u, "role", None) == UserRole.SUPER_ADMIN and getattr(u, "facility_id", None) is None
