from django.contrib import admin
from .models import Patient, PatientDocument, HMO, Allergy

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
admin.site.register(HMO)