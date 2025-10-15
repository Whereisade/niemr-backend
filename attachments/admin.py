from django.contrib import admin
from .models import File, AttachmentLink

@admin.register(File)
class FileAdmin(admin.ModelAdmin):
    list_display = ("id","original_name","facility","patient","visibility","tag","size_bytes","created_at")
    list_filter = ("facility","visibility","tag")
    search_fields = ("original_name","sha256")

@admin.register(AttachmentLink)
class AttachmentLinkAdmin(admin.ModelAdmin):
    list_display = ("file","content_type","object_id","created_at")
    list_filter = ("content_type",)
