from django.contrib import admin
from .models import Encounter, EncounterAmendment

@admin.register(Encounter)
class EncounterAdmin(admin.ModelAdmin):
    list_display = ("id","patient","facility","occurred_at","status","created_by","locked_flag","created_at")
    list_filter  = ("status","facility")
    search_fields = ("chief_complaint","diagnoses","plan")

    def locked_flag(self, obj):
        return obj.is_locked
    locked_flag.boolean = True
    locked_flag.short_description = "Locked?"

@admin.register(EncounterAmendment)
class EncounterAmendmentAdmin(admin.ModelAdmin):
    list_display = ("id","encounter","added_by","reason","created_at")
    search_fields = ("reason","content")
    readonly_fields = ("encounter","added_by","reason","content","created_at")

    def has_add_permission(self, request):
        # Prefer creating via API to ensure proper audit trail; block admin add.
        return False

    def has_change_permission(self, request, obj=None):
        # Append-only: no edits after creation
        return False

    def has_delete_permission(self, request, obj=None):
        # Append-only: never delete
        return False
