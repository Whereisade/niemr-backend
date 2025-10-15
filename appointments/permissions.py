from rest_framework.permissions import BasePermission
from accounts.enums import UserRole

STAFF = {
    UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.DOCTOR, UserRole.NURSE,
    UserRole.LAB, UserRole.PHARMACY, UserRole.FRONTDESK
}

class IsStaff(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in STAFF)

class CanViewAppointment(BasePermission):
    """
    Patient: view own appointments.
    Staff: view by facility.
    """
    def has_object_permission(self, request, view, obj):
        u = request.user
        if not u or not u.is_authenticated:
            return False
        if obj.patient.user_id == getattr(u, "id", None):
            return True
        if u.facility_id and obj.facility_id == u.facility_id and u.role in STAFF:
            return True
        return False
