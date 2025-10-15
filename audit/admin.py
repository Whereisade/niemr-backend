from django.contrib import admin
from .models import AuditLog

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("id","verb","actor","actor_email","target_ct","target_id","created_at")
    list_filter = ("verb","target_ct")
    search_fields = ("actor_email","message","changes","extra")
    readonly_fields = [f.name for f in AuditLog._meta.fields]
