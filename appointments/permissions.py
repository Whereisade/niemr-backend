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
    Provider (facility or independent): view appointments assigned to them.
    """
    def has_object_permission(self, request, view, obj):
        u = request.user
        if not u or not u.is_authenticated:
            return False

        # ✅ Patient can view own appointments (including dependents)
        if u.role == UserRole.PATIENT:
            base_patient = getattr(u, "patient_profile", None)
            if base_patient:
                if obj.patient_id == getattr(base_patient, "id", None):
                    return True
                # dependent (child) appointment
                if getattr(obj.patient, "parent_patient_id", None) == getattr(base_patient, "id", None):
                    return True
            # fallback (legacy)
            if getattr(obj.patient, "user_id", None) == getattr(u, "id", None):
                return True

        # ✅ Provider can view appointments assigned to them (works for independent providers too)
        if obj.provider_id and obj.provider_id == getattr(u, "id", None) and u.role in STAFF:
            return True

        # ✅ Facility staff can view facility appointments
        if u.facility_id and obj.facility_id == u.facility_id and u.role in STAFF:
            return True

        return False
