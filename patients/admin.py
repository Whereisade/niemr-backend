from django.contrib import admin
from .models import Patient, PatientDocument, HMO

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("last_name","first_name","dob","facility","insurance_status","created_at")
    search_fields = ("last_name","first_name","email","phone")
    list_filter = ("facility","insurance_status","patient_status","blood_group","genotype")

admin.site.register(PatientDocument)
admin.site.register(HMO)
