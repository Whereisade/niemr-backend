from rest_framework.permissions import BasePermission, SAFE_METHODS
from accounts.enums import UserRole

class IsSelfOrFacilityStaff(BasePermission):
    """
    Allow:
    - patient to view/edit their own Patient record
    - facility staff (ADMIN/DOCTOR/NURSE/LAB/PHARMACY/FRONTDESK/SUPER_ADMIN) to access patients within their facility
    """
    staff_roles = {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.DOCTOR, UserRole.NURSE, UserRole.LAB, UserRole.PHARMACY, UserRole.FRONTDESK}

    def has_object_permission(self, request, view, obj):
        u = request.user
        if not u or not u.is_authenticated:
            return False
        if obj.user_id == getattr(u, "id", None):
            return True
        if u.role in self.staff_roles and u.facility_id and obj.facility_id == u.facility_id:
            return True

        # Independent staff (no facility): allow access only if the patient is related to the user
        # via appointments/encounters/labs/prescriptions.
        if u.role in self.staff_roles and not u.facility_id:
            uid = getattr(u, "id", None)
            if not uid:
                return False

            return (
                obj.appointments.filter(provider_id=uid).exists()
                or obj.encounters.filter(created_by_id=uid).exists()
                or obj.encounters.filter(provider_id=uid).exists()
                or obj.encounters.filter(nurse_id=uid).exists()
                or obj.lab_orders.filter(ordered_by_id=uid).exists()
                or obj.lab_orders.filter(outsourced_to_id=uid).exists()
                or obj.prescriptions.filter(prescribed_by_id=uid).exists()
                or obj.prescriptions.filter(outsourced_to_id=uid).exists()
                or obj.provider_links.filter(provider_id=uid).exists()
            )

        return False

class IsStaff(BasePermission):
    staff_roles = {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.DOCTOR, UserRole.NURSE, UserRole.LAB, UserRole.PHARMACY, UserRole.FRONTDESK}
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in self.staff_roles)

class IsStaffOrGuardianForDependent(BasePermission):
    """
    Staff can access dependents within their facility/tenant (enforced in queryset).
    A patient (guardian) can only access dependents where they are the parent_patient.
    """

    message = "Not permitted to access this dependent."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        # Staff & clinical users: let the view's queryset scoping do the heavy lifting
        if getattr(user, "is_staff", False) or getattr(user, "is_clinician", False) or getattr(user, "is_admin", False):
            return True

        # Patient/guardian:
        patient_profile = getattr(user, "patient_profile", None)
        if not patient_profile:
            return False

        # obj is a Patient instance representing the dependent
        if obj.parent_patient_id and obj.parent_patient_id == patient_profile.id:
            return True

        return False


class IsStaffOrSelfPatient(BasePermission):
    """
    For /patients/{id}/dependents/ actions: allow staff OR the patient owner of {id}.
    """

    message = "Not permitted to manage dependents for this patient."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        if getattr(user, "is_staff", False) or getattr(user, "is_clinician", False) or getattr(user, "is_admin", False):
            return True

        # For patient users: they must own the path patient_id
        patient_profile = getattr(user, "patient_profile", None)
        if not patient_profile:
            return False

        path_patient_id = view.kwargs.get("pk") or view.kwargs.get("patient_pk")
        return str(patient_profile.id) == str(path_patient_id)
