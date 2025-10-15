from django.contrib import admin
from .models import ReportJob

@admin.register(ReportJob)
class ReportJobAdmin(admin.ModelAdmin):
    list_display = ("id","report_type","ref_id","created_by","format","saved_as_attachment_id","created_at")
    list_filter = ("report_type","format")
    search_fields = ("ref_id",)
