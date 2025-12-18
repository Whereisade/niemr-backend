from rest_framework.permissions import BasePermission
from accounts.enums import UserRole

STAFF_ROLES = {
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
        return bool(u and u.is_authenticated and u.role in STAFF_ROLES)


class CanViewEncounter(BasePermission):
    """
    Patient can view own encounters.
    Facility staff can view encounters in their facility.
    Independent staff (no facility) can view encounters they created.
    """

    def has_object_permission(self, request, view, obj):
        u = request.user
        if not u or not u.is_authenticated:
            return False

        # patient: own
        if obj.patient.user_id == getattr(u, "id", None):
            return True

        # facility staff: same facility
        if u.role in STAFF_ROLES and u.facility_id and obj.facility_id == u.facility_id:
            return True

        # independent staff: own created encounters only
        if u.role in STAFF_ROLES and not u.facility_id and obj.created_by_id == getattr(u, "id", None):
            return True

        return False
