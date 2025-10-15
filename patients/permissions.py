from rest_framework.permissions import BasePermission, SAFE_METHODS
from accounts.enums import UserRole

class IsSelfOrFacilityStaff(BasePermission):
    """
    Allow:
    - patient to view/edit their own Patient record
    - facility staff (ADMIN/DOCTOR/NURSE/LAB/PHARMACY/FRONTDESK/SUPER_ADMIN) to access patients within their facility
    """
    staff_roles = {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.DOCTOR, UserRole.NURSE, UserRole.LAB, UserRole.PHARMACY, UserRole.FRONTDESK}

    def has_object_permission(self, request, view, obj):
        u = request.user
        if not u or not u.is_authenticated:
            return False
        if obj.user_id == getattr(u, "id", None):
            return True
        if u.role in self.staff_roles and u.facility_id and obj.facility_id == u.facility_id:
            return True
        return False

class IsStaff(BasePermission):
    staff_roles = {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.DOCTOR, UserRole.NURSE, UserRole.LAB, UserRole.PHARMACY, UserRole.FRONTDESK}
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in self.staff_roles)
