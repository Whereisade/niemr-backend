from django.contrib import admin
from .models import ProviderProfile, ProviderDocument

@admin.register(ProviderProfile)
class ProviderProfileAdmin(admin.ModelAdmin):
    list_display = ("user","provider_type","license_council","license_number","verification_status","state","created_at")
    list_filter = ("provider_type","verification_status","license_council","state")
    search_fields = ("user__first_name","user__last_name","user__email","license_number","bio")

@admin.register(ProviderDocument)
class ProviderDocumentAdmin(admin.ModelAdmin):
    list_display = ("profile","kind","file","uploaded_at")
