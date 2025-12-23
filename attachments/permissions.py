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


        # Independent staff (no facility): allow access to files they uploaded or that belong to
        # patients related to them. INTERNAL visibility remains admin-only.
        if u.role in STAFF and not u.facility_id:
            if obj.visibility == Visibility.INTERNAL and u.role not in (UserRole.SUPER_ADMIN, UserRole.ADMIN):
                return False

            uid = getattr(u, "id", None)
            if not uid:
                return False

            if obj.uploaded_by_id == uid:
                return True

            patient = getattr(obj, "patient", None)
            if not (patient_id := getattr(patient, "id", None)):
                return False

            # Relationship checks via patient reverse relations
            return (
                patient.appointments.filter(provider_id=uid).exists()
                or patient.encounters.filter(created_by_id=uid).exists()
                or patient.encounters.filter(provider_id=uid).exists()
                or patient.encounters.filter(nurse_id=uid).exists()
                or patient.lab_orders.filter(ordered_by_id=uid).exists()
                or patient.lab_orders.filter(outsourced_to_id=uid).exists()
                or patient.prescriptions.filter(prescribed_by_id=uid).exists()
                or patient.prescriptions.filter(outsourced_to_id=uid).exists()
            )

        # INTERNAL docs only for admins anywhere (optional tighten by facility)
        if obj.visibility == Visibility.INTERNAL and u.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN):
            return True

        return False

class IsStaff(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in STAFF)
