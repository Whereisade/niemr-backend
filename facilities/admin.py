from django.contrib import admin
from .models import Facility, Ward, Bed, Specialty, FacilityExtraDocument

@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    list_display = ("name","facility_type","country","state","lga","email","phone","nhis_approved","created_at")
    search_fields = ("name","email","registration_number","state","lga")
    list_filter = ("facility_type","nhis_approved","country","state")

admin.site.register(Ward)
admin.site.register(Bed)
admin.site.register(Specialty)
admin.site.register(FacilityExtraDocument)
