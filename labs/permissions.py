from rest_framework.permissions import BasePermission

from accounts.enums import UserRole

# Staff roles in this system
STAFF_ROLES = {
    "SUPER_ADMIN",
    "ADMIN",
    "DOCTOR",
    "NURSE",
    "LAB",
    "PHARMACY",
    "FRONTDESK",
}


class IsStaff(BasePermission):
    """Generic staff permission for lab actions like create/cancel."""

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False

        role = (getattr(user, "role", "") or "").upper()
        return role in STAFF_ROLES


class CanViewLabOrder(BasePermission):
    """
    - Patients: own orders
    - Facility staff: orders in same facility
    - Independent staff: orders they created
    - Independent labs: orders assigned to them (outsourced_to)
    - Admin/Super Admin: all
    """

    def has_object_permission(self, request, view, obj):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False

        role = (getattr(user, "role", "") or "").upper()
        facility_id = getattr(user, "facility_id", None)

        if role in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
            return True

        patient = getattr(obj, "patient", None)

        # Patient: can view own records, plus dependents (if guardian)
        if role == UserRole.PATIENT and patient is not None:
            base_patient = getattr(user, "patient_profile", None)
            if base_patient and (getattr(patient, "id", None) == getattr(base_patient, "id", None) or getattr(patient, "parent_patient_id", None) == getattr(base_patient, "id", None)):
                return True

        # Patient legacy: direct user linkage
        if patient is not None and getattr(patient, "user_id", None) == getattr(user, "id", None):
            return True

        if facility_id and getattr(obj, "facility_id", None) == facility_id and role in STAFF_ROLES:
            return True

        if role == UserRole.LAB and not facility_id and getattr(obj, "outsourced_to_id", None) == getattr(user, "id", None):
            return True

        if role in STAFF_ROLES and not facility_id and getattr(obj, "ordered_by_id", None) == getattr(user, "id", None):
            return True

        return False


class IsLabOrAdmin(BasePermission):
    """
    LAB/ADMIN/SUPER_ADMIN can do lab work.
    For outsourced orders, ONLY the assigned independent lab can work it.
    """

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        role = (getattr(user, "role", "") or "").upper()
        return role in {UserRole.LAB, UserRole.ADMIN, UserRole.SUPER_ADMIN}

    def has_object_permission(self, request, view, obj):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False

        role = (getattr(user, "role", "") or "").upper()
        if role in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
            return True

        if role != UserRole.LAB:
            return False

        if getattr(obj, "outsourced_to_id", None):
            return getattr(obj, "outsourced_to_id", None) == getattr(user, "id", None)

        # Independent labs (no facility) may work on orders they created themselves.
        if not getattr(user, "facility_id", None) and getattr(obj, "ordered_by_id", None) == getattr(user, "id", None):
            return True

        return bool(getattr(user, "facility_id", None) and getattr(obj, "facility_id", None) == user.facility_id)
