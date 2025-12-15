from rest_framework.permissions import BasePermission

# Simple string-based roles; we normalize user.role to uppercase when checking.
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
    """
    Generic staff permission for lab actions like create/collect/result.
    """

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False

        role = (getattr(user, "role", "") or "").upper()
        return role in STAFF_ROLES


class CanViewLabOrder(BasePermission):
    """
    Object-level permission for viewing a single LabOrder.

    - Patients: can view their own orders.
    - Staff (DOCTOR/NURSE/LAB/PHARMACY/FRONTDESK/ADMIN/SUPER_ADMIN)
      can view orders in their facility.
    """

    def has_object_permission(self, request, view, obj):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False

        role = (getattr(user, "role", "") or "").upper()
        facility_id = getattr(user, "facility_id", None)

        # Patients: user is the same as the LabOrder.patient.user
        patient = getattr(obj, "patient", None)
        if patient is not None and getattr(patient, "user_id", None) == getattr(
            user, "id", None
        ):
            return True

        # Staff tied to a facility: can see orders for that facility
        if (
            facility_id
            and getattr(obj, "facility_id", None) == facility_id
            and role in STAFF_ROLES
        ):
            return True

        return False


class IsLabOrAdmin(BasePermission):
    """
    Only Lab Scientists + Admin/Super Admin can perform lab *work*
    (collect samples, enter results).
    """

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        role = (getattr(user, "role", "") or "").upper()
        return role in {"LAB", "ADMIN", "SUPER_ADMIN"}
