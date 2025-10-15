from django.contrib import admin
from .models import ImagingProcedure, ImagingRequest, ImagingReport, ImagingAsset

@admin.register(ImagingProcedure)
class ImagingProcedureAdmin(admin.ModelAdmin):
    list_display = ("code","name","modality","price","is_active")
    list_filter = ("modality","is_active")
    search_fields = ("code","name")

class AssetInline(admin.TabularInline):
    model = ImagingAsset
    extra = 0

@admin.register(ImagingReport)
class ImagingReportAdmin(admin.ModelAdmin):
    list_display = ("id","request","reported_by","reported_at")
    inlines = [AssetInline]

@admin.register(ImagingRequest)
class ImagingRequestAdmin(admin.ModelAdmin):
    list_display = ("id","patient","facility","procedure","priority","status","requested_at","scheduled_for")
    list_filter = ("status","priority","procedure__modality","facility")
    search_fields = ("patient__first_name","patient__last_name","procedure__name","procedure__code")
