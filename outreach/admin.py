from django.contrib import admin
from .models import (
    OutreachEvent, OutreachSite, OutreachStaffProfile, OutreachPatient,
    OutreachVitals, OutreachEncounter,
    OutreachLabTestCatalog, OutreachLabOrder, OutreachLabResult,
    OutreachDrugCatalog, OutreachDispense,
    OutreachImmunization, OutreachBloodDonation, OutreachCounseling, OutreachMaternal,
    OutreachAuditLog, OutreachExport,
)

@admin.register(OutreachEvent)
class OutreachEventAdmin(admin.ModelAdmin):
    list_display = ("id","title","status","starts_at","ends_at","created_at","closed_at")
    search_fields = ("title",)

@admin.register(OutreachSite)
class OutreachSiteAdmin(admin.ModelAdmin):
    list_display = ("id","outreach_event","name","community","is_active","created_at")
    search_fields = ("name","community")
    list_filter = ("is_active",)

@admin.register(OutreachStaffProfile)
class OutreachStaffProfileAdmin(admin.ModelAdmin):
    list_display = ("id","outreach_event","user","role_template","is_active","created_at")
    list_filter = ("is_active","role_template")
    search_fields = ("user__email",)

@admin.register(OutreachPatient)
class OutreachPatientAdmin(admin.ModelAdmin):
    list_display = ("id","outreach_event","patient_code","full_name","sex","phone","site","created_at")
    search_fields = ("patient_code","full_name","phone")
    list_filter = ("sex",)

admin.site.register(OutreachVitals)
admin.site.register(OutreachEncounter)
admin.site.register(OutreachLabTestCatalog)
admin.site.register(OutreachLabOrder)
admin.site.register(OutreachLabResult)
admin.site.register(OutreachDrugCatalog)
admin.site.register(OutreachDispense)
admin.site.register(OutreachImmunization)
admin.site.register(OutreachBloodDonation)
admin.site.register(OutreachCounseling)
admin.site.register(OutreachMaternal)
admin.site.register(OutreachAuditLog)
admin.site.register(OutreachExport)
