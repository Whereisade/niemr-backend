from rest_framework.permissions import BasePermission
from accounts.enums import UserRole

ADMIN_ROLES = {UserRole.SUPER_ADMIN, UserRole.ADMIN}

class IsSelfProvider(BasePermission):
    def has_object_permission(self, request, view, obj):
        return bool(request.user and request.user.is_authenticated and obj.user_id == request.user.id)

class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in ADMIN_ROLES)
