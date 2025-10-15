from rest_framework.permissions import BasePermission
from .enums import UserRole

class IsRole(BasePermission):
    required_roles: tuple[str,...] = ()
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role in self.required_roles)

class IsAdmin(IsRole): required_roles = (UserRole.SUPER_ADMIN, UserRole.ADMIN)
class IsDoctor(IsRole): required_roles = (UserRole.DOCTOR,)
class IsNurse(IsRole):  required_roles = (UserRole.NURSE,)
# add others as needed
class IsLab(IsRole):       required_roles = (UserRole.LAB,)
class IsPharmacy(IsRole):  required_roles = (UserRole.PHARMACY,)
class IsFrontDesk(IsRole): required_roles = (UserRole.FRONTDESK,)
class IsPatient(IsRole):   required_roles = (UserRole.PATIENT,)