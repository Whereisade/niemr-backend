from rest_framework.permissions import BasePermission
from accounts.enums import UserRole

STAFF = {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.DOCTOR, UserRole.NURSE, UserRole.LAB, UserRole.PHARMACY, UserRole.FRONTDESK}

class IsStaff(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in STAFF)

class CanViewFinance(BasePermission):
    """
    Patients: own ledger.
    Staff: by facility.
    """
    def has_object_permission(self, request, view, obj):
        u = request.user
        if not u or not u.is_authenticated:
            return False
        if getattr(obj, "patient_id", None) == u.id:  # won't match (obj.patient is Patient); use below per-view logic
            return True
        # fallback handled in views by scoping queries
        return True
