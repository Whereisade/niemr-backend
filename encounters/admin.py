from django.contrib import admin
from .models import Encounter, EncounterAmendment

@admin.register(Encounter)
class EncounterAdmin(admin.ModelAdmin):
    list_display = ("id","patient","facility","occurred_at","status","created_by","is_locked_display","created_at")
    list_filter  = ("status","facility")
    search_fields = ("chief_complaint","diagnoses","plan")

    def is_locked_display(self, obj):
        return obj.is_locked
    is_locked_display.boolean = True
    is_locked_display.short_description = "Locked?"

@admin.register(EncounterAmendment)
class EncounterAmendmentAdmin(admin.ModelAdmin):
    list_display = ("id","encounter","added_by","reason","created_at")
    search_fields = ("reason","content")
