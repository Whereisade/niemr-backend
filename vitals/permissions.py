from rest_framework.permissions import BasePermission
from accounts.enums import UserRole

STAFF_ROLES = {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.DOCTOR, UserRole.NURSE, UserRole.LAB, UserRole.PHARMACY, UserRole.FRONTDESK}

class IsStaff(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in STAFF_ROLES)

class CanViewVitals(BasePermission):
    """
    Patient can view their own vitals.
    Facility staff can view vitals for patients in their facility.
    Independent providers can view vitals they recorded or for their patients.
    """
    def has_object_permission(self, request, view, obj):
        u = request.user
        if not u or not u.is_authenticated:
            return False
        
        # Patient owns the linked user
        if obj.patient.user_id == getattr(u, "id", None):
            return True
        
        # Facility staff: same facility
        if u.role in STAFF_ROLES and u.facility_id and obj.facility_id == u.facility_id:
            return True
        
        # Independent provider: vitals they recorded OR for their patients
        if u.role in {UserRole.DOCTOR, UserRole.NURSE} and not u.facility_id:
            # Check if they recorded these vitals
            if obj.recorded_by_id == u.id:
                return True
            # Check if they've seen this patient in an encounter (as provider or nurse)
            if obj.patient.encounters.filter(
                Q(provider=u) | Q(nurse=u)
            ).exists():
                return True
            # Check if patient is explicitly linked to them
            if obj.patient.provider_links.filter(provider=u).exists():
                return True
        
        return False