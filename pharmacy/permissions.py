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

PHARMACY_ROLES = {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.PHARMACY}
PRESCRIBER_ROLES = {UserRole.DOCTOR, UserRole.NURSE, UserRole.PHARMACY}


class IsStaff(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in STAFF)


class CanViewRx(BasePermission):
    """
    Patient: own prescriptions.
    Facility staff: prescriptions within facility.
    Independent prescriber: prescriptions they created.
    Independent pharmacy: prescriptions assigned to them (outsourced_to).
    Admin/Super Admin: all.
    """
    def has_object_permission(self, request, view, obj):
        u = request.user
        if not u or not u.is_authenticated:
            return False

        role = (getattr(u, "role", "") or "").upper()

        if role in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
            return True

        # patient owns
        if obj.patient.user_id == getattr(u, "id", None):
            return True

        # facility staff scope
        if u.facility_id and obj.facility_id == u.facility_id and u.role in STAFF:
            return True

        # independent pharmacy: assigned only
        if u.role == UserRole.PHARMACY and not u.facility_id and obj.outsourced_to_id == u.id:
            return True

        # independent staff: only what they prescribed
        if u.role in STAFF and not u.facility_id and obj.prescribed_by_id == u.id:
            return True

        return False


class IsPharmacyStaff(BasePermission):
    message = "Only pharmacy staff can perform this action."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in PHARMACY_ROLES)


class CanPrescribe(BasePermission):
    message = "You are not allowed to create prescriptions."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in PRESCRIBER_ROLES)
