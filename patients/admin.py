# patients/admin.py
from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Patient, PatientDocument, HMO, Allergy, PatientProviderLink,
    SystemHMO, HMOTier, FacilityHMO, PatientFacilityHMOApproval
)


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = (
        "last_name", "first_name", "dob", "facility", 
        "insurance_status", "system_hmo_display", "hmo_tier_display", "created_at"
    )
    search_fields = ("last_name", "first_name", "email", "phone", "insurance_number")
    list_filter = ("facility", "insurance_status", "patient_status", "blood_group", "genotype", "system_hmo")
    raw_id_fields = ("user", "facility", "guardian_user", "parent_patient", "hmo", "system_hmo", "hmo_tier")
    readonly_fields = ("created_at", "updated_at", "bmi")
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('first_name', 'last_name', 'middle_name', 'dob', 'gender')
        }),
        ('Contact', {
            'fields': ('email', 'phone', 'country', 'state', 'lga', 'address')
        }),
        ('Account & Facility', {
            'fields': ('user', 'facility', 'guardian_user', 'parent_patient', 'relationship_to_guardian')
        }),
        ('Insurance (Legacy)', {
            'fields': ('insurance_status', 'hmo', 'hmo_plan'),
            'classes': ('collapse',),
            'description': 'Legacy facility-scoped HMO (deprecated)'
        }),
        ('Insurance (System HMO)', {
            'fields': (
                'system_hmo', 'hmo_tier', 'insurance_number', 
                'insurance_expiry', 'insurance_notes',
                'hmo_enrollment_facility', 'hmo_enrollment_provider', 'hmo_enrolled_at'
            ),
        }),
        ('Clinical', {
            'fields': ('blood_group', 'blood_group_other', 'genotype', 'genotype_other', 'weight_kg', 'height_cm', 'bmi'),
            'classes': ('collapse',)
        }),
        ('Status', {
            'fields': ('patient_status', 'default_encounter_type')
        }),
        ('Emergency Contact', {
            'fields': ('emergency_contact_name', 'emergency_contact_phone'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def system_hmo_display(self, obj):
        if obj.system_hmo:
            return obj.system_hmo.name
        return "-"
    system_hmo_display.short_description = "System HMO"
    
    def hmo_tier_display(self, obj):
        if obj.hmo_tier:
            return obj.hmo_tier.name
        return "-"
    hmo_tier_display.short_description = "Tier"


@admin.register(Allergy)
class AllergyAdmin(admin.ModelAdmin):
    list_display = ("allergen", "patient", "allergy_type", "severity", "is_active", "created_at")
    search_fields = ("allergen", "patient__first_name", "patient__last_name", "reaction")
    list_filter = ("allergy_type", "severity", "is_active", "created_at")
    raw_id_fields = ("patient", "recorded_by")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


admin.site.register(PatientDocument)


@admin.register(PatientProviderLink)
class PatientProviderLinkAdmin(admin.ModelAdmin):
    list_display = ("patient", "provider", "created_at")
    search_fields = (
        "patient__first_name",
        "patient__last_name",
        "provider__email",
        "provider__first_name",
        "provider__last_name",
    )
    list_filter = ("created_at",)
    raw_id_fields = ("patient", "provider")


@admin.register(HMO)
class HMOAdmin(admin.ModelAdmin):
    """
    Admin for legacy facility-scoped HMOs.
    """
    list_display = [
        'name',
        'facility',
        'nhis_number',
        'email',
        'get_address_count',
        'get_phone_count',
        'is_active',
        'created_at',
    ]
    
    list_filter = ['is_active', 'facility', 'created_at']
    search_fields = ['name', 'nhis_number', 'email', 'contact_person_name']
    raw_id_fields = ['facility', 'relationship_updated_by']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('facility', 'name', 'nhis_number', 'is_active')
        }),
        ('Contact Information', {
            'fields': ('email', 'addresses', 'contact_numbers'),
            'description': 'Primary contact details for the HMO'
        }),
        ('Contact Person', {
            'fields': (
                'contact_person_name',
                'contact_person_phone',
                'contact_person_email',
            ),
            'classes': ('collapse',),
        }),
        ('Relationship Status', {
            'fields': (
                'relationship_status',
                'relationship_notes',
                'relationship_updated_at',
                'relationship_updated_by',
            ),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    
    def get_address_count(self, obj):
        if obj.addresses:
            count = len(obj.addresses)
            return f"{count} address{'es' if count != 1 else ''}"
        return "0 addresses"
    get_address_count.short_description = 'Addresses'
    
    def get_phone_count(self, obj):
        if obj.contact_numbers:
            count = len(obj.contact_numbers)
            return f"{count} number{'s' if count != 1 else ''}"
        return "0 numbers"
    get_phone_count.short_description = 'Phone Numbers'


# ============================================================================
# SYSTEM HMO ADMIN
# ============================================================================

class HMOTierInline(admin.TabularInline):
    """Inline admin for HMO tiers."""
    model = HMOTier
    extra = 0
    fields = ['name', 'level', 'description', 'is_active']
    readonly_fields = ['level']  # Tiers are created automatically, level is fixed
    ordering = ['level']
    
    def has_add_permission(self, request, obj=None):
        # Tiers are auto-created, don't allow manual addition
        return False
    
    def has_delete_permission(self, request, obj=None):
        # Don't allow deleting tiers (deactivate instead)
        return False


@admin.register(SystemHMO)
class SystemHMOAdmin(admin.ModelAdmin):
    """
    Admin for System HMOs (master list).
    """
    list_display = [
        'name',
        'nhis_number',
        'email',
        'tier_count',
        'facility_count',
        'is_active',
        'created_at',
    ]
    
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'nhis_number', 'email']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'nhis_number', 'is_active')
        }),
        ('Contact Information', {
            'fields': ('email', 'addresses', 'contact_numbers'),
        }),
        ('Contact Person', {
            'fields': (
                'contact_person_name',
                'contact_person_phone',
                'contact_person_email',
            ),
            'classes': ('collapse',),
        }),
        ('Additional Info', {
            'fields': ('website', 'description'),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    
    inlines = [HMOTierInline]
    
    def tier_count(self, obj):
        """Display number of active tiers."""
        count = obj.tiers.filter(is_active=True).count()
        return f"{count} tiers"
    tier_count.short_description = 'Tiers'
    
    def facility_count(self, obj):
        """Display number of facilities using this HMO."""
        count = obj.facility_links.filter(is_active=True).count()
        return f"{count} facilities"
    facility_count.short_description = 'Enabled At'


@admin.register(HMOTier)
class HMOTierAdmin(admin.ModelAdmin):
    """
    Admin for HMO Tiers.
    """
    list_display = ['__str__', 'system_hmo', 'name', 'level', 'is_active']
    list_filter = ['system_hmo', 'level', 'is_active']
    search_fields = ['name', 'system_hmo__name']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['system_hmo']
    
    def has_add_permission(self, request):
        # Tiers are auto-created when SystemHMO is created
        return False


@admin.register(FacilityHMO)
class FacilityHMOAdmin(admin.ModelAdmin):
    """
    Admin for Facility-HMO relationships.
    """
    list_display = [
        'id',
        'scope_display',
        'system_hmo',
        'relationship_status_badge',
        'is_active',
        'created_at',
    ]
    
    list_filter = [
        'is_active',
        'relationship_status',
        'system_hmo',
    ]
    
    search_fields = [
        'facility__name',
        'system_hmo__name',
        'owner__email',
    ]
    
    readonly_fields = [
        'created_at',
        'updated_at',
        'relationship_updated_at',
    ]
    
    raw_id_fields = ['facility', 'owner', 'system_hmo', 'relationship_updated_by']
    
    fieldsets = (
        ('Scope', {
            'fields': ('facility', 'owner', 'system_hmo'),
        }),
        ('Relationship', {
            'fields': (
                'relationship_status',
                'relationship_notes',
                'relationship_updated_at',
                'relationship_updated_by',
            ),
        }),
        ('Contract', {
            'fields': (
                'contract_start_date',
                'contract_end_date',
                'contract_reference',
            ),
            'classes': ('collapse',),
        }),
        ('Status', {
            'fields': ('is_active',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    
    def scope_display(self, obj):
        """Display facility or provider."""
        if obj.facility:
            return f"🏥 {obj.facility.name}"
        if obj.owner:
            name = f"{obj.owner.first_name} {obj.owner.last_name}".strip()
            return f"👤 {name or obj.owner.email}"
        return "-"
    scope_display.short_description = 'Scope'
    
    def relationship_status_badge(self, obj):
        """Display relationship status with color."""
        colors = {
            'EXCELLENT': '#10b981',  # green
            'GOOD': '#3b82f6',  # blue
            'FAIR': '#f59e0b',  # yellow
            'POOR': '#f97316',  # orange
            'BAD': '#ef4444',  # red
        }
        color = colors.get(obj.relationship_status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 4px; font-size: 11px;">{}</span>',
            color,
            obj.get_relationship_status_display()
        )
    relationship_status_badge.short_description = 'Status'


@admin.register(PatientFacilityHMOApproval)
class PatientFacilityHMOApprovalAdmin(admin.ModelAdmin):
    """
    Admin for Patient HMO Transfer Approvals.
    """
    list_display = [
        'id',
        'patient',
        'system_hmo',
        'tier',
        'scope_display',
        'status_badge',
        'requested_at',
    ]
    
    list_filter = [
        'status',
        'system_hmo',
        'tier',
    ]
    
    search_fields = [
        'patient__first_name',
        'patient__last_name',
        'system_hmo__name',
        'insurance_number',
    ]
    
    readonly_fields = [
        'created_at',
        'updated_at',
        'requested_at',
        'decided_at',
    ]
    
    raw_id_fields = [
        'patient', 'facility', 'owner', 'system_hmo', 'tier',
        'original_facility', 'original_provider', 'decided_by'
    ]
    
    fieldsets = (
        ('Patient', {
            'fields': ('patient',),
        }),
        ('Requesting At', {
            'fields': ('facility', 'owner'),
        }),
        ('HMO Details', {
            'fields': (
                'system_hmo',
                'tier',
                'insurance_number',
                'insurance_expiry',
            ),
        }),
        ('Original Enrollment', {
            'fields': ('original_facility', 'original_provider'),
            'classes': ('collapse',),
        }),
        ('Approval', {
            'fields': (
                'status',
                'request_notes',
                'decision_notes',
                'decided_by',
                'decided_at',
            ),
        }),
        ('Timestamps', {
            'fields': ('requested_at', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    
    def scope_display(self, obj):
        """Display facility or provider."""
        if obj.facility:
            return f"🏥 {obj.facility.name}"
        if obj.owner:
            name = f"{obj.owner.first_name} {obj.owner.last_name}".strip()
            return f"👤 {name or obj.owner.email}"
        return "-"
    scope_display.short_description = 'At'
    
    def status_badge(self, obj):
        """Display status with color."""
        colors = {
            'PENDING': '#f59e0b',  # yellow
            'APPROVED': '#10b981',  # green
            'REJECTED': '#ef4444',  # red
        }
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 4px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    
    actions = ['approve_selected', 'reject_selected']
    
    @admin.action(description='Approve selected requests')
    def approve_selected(self, request, queryset):
        count = 0
        for approval in queryset.filter(status='PENDING'):
            approval.approve(request.user, 'Approved via admin')
            count += 1
        self.message_user(request, f'Approved {count} requests.')
    
    @admin.action(description='Reject selected requests')
    def reject_selected(self, request, queryset):
        count = 0
        for approval in queryset.filter(status='PENDING'):
            approval.reject(request.user, 'Rejected via admin')
            count += 1
        self.message_user(request, f'Rejected {count} requests.')