from rest_framework.permissions import BasePermission
from accounts.enums import UserRole

STAFF = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.DOCTOR,
    UserRole.NURSE,
    UserRole.LAB,
    UserRole.PHARMACY,
    UserRole.FRONTDESK,
}


class IsStaff(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in STAFF)


class CanViewRx(BasePermission):
    """
    Patient: own prescriptions. Staff: by facility.
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


# 🔹 NEW: pharmacy-focused & prescriber-focused permissions

PHARMACY_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.PHARMACY,
}

PRESCRIBER_ROLES = {
    UserRole.DOCTOR,
    UserRole.NURSE,
    # If you later introduce other clinical prescriber roles as UserRole values,
    # add them here.
}


class IsPharmacyStaff(BasePermission):
    """
    Only pharmacy-facing staff (Pharmacy + Admins).
    """

    message = "Only pharmacy staff can perform this action."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in PHARMACY_ROLES)


class CanPrescribe(BasePermission):
    """
    Clinical prescribers allowed to CREATE prescriptions.
    """

    message = "You are not allowed to create prescriptions."

    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            return False
        return u.role in PRESCRIBER_ROLES
