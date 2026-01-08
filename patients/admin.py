from django.contrib import admin
from .models import Patient, PatientDocument, HMO, Allergy, PatientProviderLink

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("last_name","first_name","dob","facility","insurance_status","created_at")
    search_fields = ("last_name","first_name","email","phone")
    list_filter = ("facility","insurance_status","patient_status","blood_group","genotype")

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
    Enhanced admin interface for HMO management with all new fields.
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
    
    search_fields = [
        'name',
        'nhis_number',
        'email',
        'contact_person_name',
    ]
    
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
            'description': 'Designated contact person for this HMO'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    
    readonly_fields = ['created_at', 'updated_at']
    
    def get_readonly_fields(self, request, obj=None):
        """Make facility read-only after creation"""
        if obj:  # Editing existing object
            return self.readonly_fields + ['facility']
        return self.readonly_fields
    
    def get_address_count(self, obj):
        """Display number of addresses"""
        if obj.addresses:
            count = len(obj.addresses)
            return f"{count} address{'es' if count != 1 else ''}"
        return "0 addresses"
    get_address_count.short_description = 'Addresses'
    
    def get_phone_count(self, obj):
        """Display number of phone numbers"""
        if obj.contact_numbers:
            count = len(obj.contact_numbers)
            return f"{count} number{'s' if count != 1 else ''}"
        return "0 numbers"
    get_phone_count.short_description = 'Phone Numbers'