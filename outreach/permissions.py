from __future__ import annotations

from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied

from accounts.enums import UserRole
from .models import OutreachEvent, OutreachStaffProfile
from .enums import OutreachStatus

def is_outreach_super_admin(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    return getattr(user, "role", None) == UserRole.SUPER_ADMIN and getattr(user, "facility_id", None) is None

def get_active_profiles(user):
    if not user or not getattr(user, "is_authenticated", False):
        return OutreachStaffProfile.objects.none()
    return OutreachStaffProfile.objects.select_related("outreach_event").filter(user=user, is_active=True, outreach_event__status__in=[OutreachStatus.DRAFT, OutreachStatus.ACTIVE])

def get_profile_for_event(user, event: OutreachEvent) -> OutreachStaffProfile | None:
    if not event:
        return None
    return OutreachStaffProfile.objects.filter(user=user, outreach_event=event, is_active=True).first()

def has_outreach_permission(user, event: OutreachEvent, perm_key: str) -> bool:
    if is_outreach_super_admin(user):
        return True
    profile = get_profile_for_event(user, event)
    if not profile:
        return False
    perms = profile.permissions or []
    return perm_key in perms

def ensure_outreach_writeable(event: OutreachEvent):
    if not event:
        raise PermissionDenied("Outreach event not found.")
    if event.status == OutreachStatus.CLOSED:
        raise PermissionDenied("This outreach is closed and is now read-only.")

def ensure_module_enabled(event: OutreachEvent, module_key: str):
    if not event:
        raise PermissionDenied("Outreach event not found.")
    if not event.is_module_enabled(module_key):
        raise PermissionDenied(f"'{module_key}' module is disabled for this outreach.")

class IsOutreachSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return is_outreach_super_admin(getattr(request, "user", None))

class IsOutreachStaff(BasePermission):
    """Allows staff if they have at least one active staff profile."""
    def has_permission(self, request, view):
        u = getattr(request, "user", None)
        if is_outreach_super_admin(u):
            return True
        return get_active_profiles(u).exists()
