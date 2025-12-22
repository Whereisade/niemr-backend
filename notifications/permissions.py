from rest_framework.permissions import BasePermission
from accounts.enums import UserRole

class IsOwner(BasePermission):
    def has_object_permission(self, request, view, obj):
        return bool(request.user and request.user.is_authenticated and obj.user_id == request.user.id)


class CanBroadcastFacilityAnnouncements(BasePermission):
    """Allow facility staff (admin/frontdesk) to broadcast announcements."""

    message = "You do not have permission to broadcast facility announcements."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if not getattr(user, "facility_id", None):
            return False
        return user.role in {
            UserRole.SUPER_ADMIN,
            UserRole.ADMIN,
            UserRole.FRONTDESK,
        }
