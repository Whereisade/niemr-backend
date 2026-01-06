# facilities/permissions_utils.py
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


# ============================================================================
# PERMISSION DECORATORS
# ============================================================================

# --- PHARMACY PERMISSIONS ---

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


def check_dispense_prescriptions_permission(view_method):
    """Decorator to check can_dispense_prescriptions permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_dispense_prescriptions'):
            return Response(
                {"detail": "You do not have permission to dispense prescriptions."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_view_prescriptions_permission(view_method):
    """Decorator to check can_view_prescriptions permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_view_prescriptions'):
            return Response(
                {"detail": "You do not have permission to view prescriptions."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


# --- LAB PERMISSIONS ---

def check_lab_catalog_permission(view_method):
    """Decorator to check can_manage_lab_catalog permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_manage_lab_catalog'):
            return Response(
                {"detail": "You do not have permission to manage the lab catalog."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_process_lab_orders_permission(view_method):
    """Decorator to check can_process_lab_orders permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_process_lab_orders'):
            return Response(
                {"detail": "You do not have permission to process lab orders."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_view_lab_orders_permission(view_method):
    """Decorator to check can_view_lab_orders permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_view_lab_orders'):
            return Response(
                {"detail": "You do not have permission to view lab orders."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


# --- ENCOUNTER PERMISSIONS ---

def check_create_encounters_permission(view_method):
    """Decorator to check can_create_encounters permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_create_encounters'):
            return Response(
                {"detail": "You do not have permission to create encounters."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_view_all_encounters_permission(view_method):
    """Decorator to check can_view_all_encounters permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_view_all_encounters'):
            return Response(
                {"detail": "You do not have permission to view all encounters."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_edit_encounters_permission(view_method):
    """Decorator to check can_edit_encounters permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_edit_encounters'):
            return Response(
                {"detail": "You do not have permission to edit encounters."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_close_encounters_permission(view_method):
    """Decorator to check can_close_encounters permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_close_encounters'):
            return Response(
                {"detail": "You do not have permission to close encounters."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_assign_providers_permission(view_method):
    """Decorator to check can_assign_providers permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_assign_providers'):
            return Response(
                {"detail": "You do not have permission to assign providers."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


# --- APPOINTMENT PERMISSIONS ---

def check_manage_appointments_permission(view_method):
    """Decorator to check can_manage_appointments permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_manage_appointments'):
            return Response(
                {"detail": "You do not have permission to manage appointments."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_view_all_appointments_permission(view_method):
    """Decorator to check can_view_all_appointments permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_view_all_appointments'):
            return Response(
                {"detail": "You do not have permission to view all appointments."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_check_in_appointments_permission(view_method):
    """Decorator to check can_check_in_appointments permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_check_in_appointments'):
            return Response(
                {"detail": "You do not have permission to check in appointments."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


# --- BILLING PERMISSIONS ---

def check_create_charges_permission(view_method):
    """Decorator to check can_create_charges permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_create_charges'):
            return Response(
                {"detail": "You do not have permission to create charges."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_view_billing_permission(view_method):
    """Decorator to check can_view_billing permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_view_billing'):
            return Response(
                {"detail": "You do not have permission to view billing information."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_manage_payments_permission(view_method):
    """Decorator to check can_manage_payments permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_manage_payments'):
            return Response(
                {"detail": "You do not have permission to manage payments."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


# --- PATIENT MANAGEMENT PERMISSIONS ---

def check_create_patients_permission(view_method):
    """Decorator to check can_create_patients permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_create_patients'):
            return Response(
                {"detail": "You do not have permission to create patients."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_view_all_patients_permission(view_method):
    """Decorator to check can_view_all_patients permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_view_all_patients'):
            return Response(
                {"detail": "You do not have permission to view all patients."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_edit_patient_records_permission(view_method):
    """Decorator to check can_edit_patient_records permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_edit_patient_records'):
            return Response(
                {"detail": "You do not have permission to edit patient records."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


# --- VITAL SIGNS PERMISSIONS ---

def check_record_vitals_permission(view_method):
    """Decorator to check can_record_vitals permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_record_vitals'):
            return Response(
                {"detail": "You do not have permission to record vital signs."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_view_vitals_permission(view_method):
    """Decorator to check can_view_vitals permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_view_vitals'):
            return Response(
                {"detail": "You do not have permission to view vital signs."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


# --- WARD MANAGEMENT PERMISSIONS ---

def check_manage_wards_permission(view_method):
    """Decorator to check can_manage_wards permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_manage_wards'):
            return Response(
                {"detail": "You do not have permission to manage wards."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_assign_beds_permission(view_method):
    """Decorator to check can_assign_beds permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_assign_beds'):
            return Response(
                {"detail": "You do not have permission to assign beds."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_discharge_patients_permission(view_method):
    """Decorator to check can_discharge_patients permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_discharge_patients'):
            return Response(
                {"detail": "You do not have permission to discharge patients."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


# --- SENSITIVE PERMISSIONS ---

def check_manage_hmo_pricing_permission(view_method):
    """Decorator to check can_manage_hmo_pricing permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_manage_hmo_pricing'):
            return Response(
                {"detail": "You do not have permission to manage HMO pricing."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper


def check_manage_facility_settings_permission(view_method):
    """Decorator to check can_manage_facility_settings permission."""
    def wrapper(self, request, *args, **kwargs):
        from rest_framework.response import Response
        
        if not has_facility_permission(request.user, 'can_manage_facility_settings'):
            return Response(
                {"detail": "You do not have permission to manage facility settings."},
                status=403
            )
        return view_method(self, request, *args, **kwargs)
    return wrapper