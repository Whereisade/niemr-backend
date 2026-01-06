# facilities/permissions_utils.py (NEW FILE)
"""
Facility-level permission checking utilities.

This module provides functions to check if users have specific permissions
within their facility. Falls back to allowing actions if no permissions
are configured (backward compatible with existing behavior).
"""

from accounts.enums import UserRole


def has_facility_permission(user, permission_name: str) -> bool:
    """
    Check if user has a specific permission in their facility.
    
    Args:
        user: User instance
        permission_name: Permission field name (e.g., 'can_manage_pharmacy_catalog')
    
    Returns:
        bool: True if user has permission, False otherwise
    
    Rules:
        - SUPER_ADMIN always has all permissions
        - Independent providers (no facility) always have all permissions
        - If no FacilityRolePermission exists, allow by default (backward compatible)
        - Otherwise, check the specific permission field
    """
    # Super admins always have full access
    if getattr(user, 'role', None) == UserRole.SUPER_ADMIN:
        return True
    
    # Independent providers (no facility) - allow by default
    if not getattr(user, 'facility_id', None):
        return True
    
    # Check facility-specific permissions
    try:
        from facilities.models import FacilityRolePermission
        
        perm = FacilityRolePermission.objects.get(
            facility_id=user.facility_id,
            role=user.role
        )
        
        # Get the specific permission field value
        return getattr(perm, permission_name, True)
        
    except FacilityRolePermission.DoesNotExist:
        # No permissions configured for this role = allow by default
        return True
    except Exception as e:
        # Any error = fail open (allow by default to avoid breaking existing functionality)
        print(f"Permission check error: {e}")
        return True


def get_user_permissions(user) -> dict:
    """
    Get all permissions for a user in their facility.
    
    Returns:
        dict: All permission fields and their values, or all True if no config exists
    """
    # Super admins have everything
    if getattr(user, 'role', None) == UserRole.SUPER_ADMIN:
        return _get_all_permissions(enabled=True)
    
    # Independent providers have everything
    if not getattr(user, 'facility_id', None):
        return _get_all_permissions(enabled=True)
    
    try:
        from facilities.models import FacilityRolePermission
        
        perm = FacilityRolePermission.objects.get(
            facility_id=user.facility_id,
            role=user.role
        )
        
        # Return all permission fields
        return {
            field.name: getattr(perm, field.name)
            for field in FacilityRolePermission._meta.fields
            if field.name.startswith('can_')
        }
        
    except FacilityRolePermission.DoesNotExist:
        # No config = all permissions enabled by default
        return _get_all_permissions(enabled=True)
    except Exception:
        return _get_all_permissions(enabled=True)


def _get_all_permissions(enabled=True) -> dict:
    """Get all permission fields with same boolean value."""
    from facilities.models import FacilityRolePermission
    
    return {
        field.name: enabled
        for field in FacilityRolePermission._meta.fields
        if field.name.startswith('can_')
    }


# Permission check decorators for common use cases
def check_pharmacy_catalog_permission(view_method):
    """Decorator to check can_manage_pharmacy_catalog permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_manage_pharmacy_catalog'):
            return Response(
                {"detail": "You do not have permission to manage the pharmacy catalog."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_pharmacy_stock_permission(view_method):
    """Decorator to check can_manage_pharmacy_stock permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_manage_pharmacy_stock'):
            return Response(
                {"detail": "You do not have permission to manage pharmacy stock."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper