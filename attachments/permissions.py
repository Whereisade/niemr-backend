from rest_framework.permissions import BasePermission
from accounts.enums import UserRole
from .enums import Visibility

STAFF = {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.DOCTOR, UserRole.NURSE, UserRole.LAB, UserRole.PHARMACY, UserRole.FRONTDESK}

class CanViewFile(BasePermission):
    """
    - PATIENT can view if file.patient.user == request.user OR visibility == PATIENT and the patient matches
    - STAFF can view within same facility
    - INTERNAL visibility: admins only (SUPER_ADMIN/ADMIN)
    """
    def has_object_permission(self, request, view, obj):
        u = request.user
        if not u or not u.is_authenticated:
            return False

        # patient access
        if obj.patient and obj.patient.user_id == getattr(u, "id", None):
            if obj.visibility in (Visibility.PATIENT, Visibility.PRIVATE, Visibility.INTERNAL):
                # patient's own file is viewable regardless of visibility (your policy may prefer PATIENT-only; adjust as needed)
                return True

        # staff by facility
        if u.role in STAFF and u.facility_id and obj.facility_id == u.facility_id:
            if obj.visibility != Visibility.INTERNAL or u.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN):
                return True

        # INTERNAL docs only for admins anywhere (optional tighten by facility)
        if obj.visibility == Visibility.INTERNAL and u.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN):
            return True

        return False

class IsStaff(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in STAFF)
