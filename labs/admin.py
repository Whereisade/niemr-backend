from django.contrib import admin
from .models import LabTest, LabOrder, LabOrderItem

@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    list_display = ("code","name","unit","ref_low","ref_high","price","is_active")
    search_fields = ("code","name")

class LabOrderItemInline(admin.TabularInline):
    model = LabOrderItem
    extra = 0

@admin.register(LabOrder)
class LabOrderAdmin(admin.ModelAdmin):
    list_display = ("id","patient","facility","ordered_by","priority","status","ordered_at")
    list_filter = ("status","priority","facility")
    search_fields = ("patient__first_name","patient__last_name","items__test__name","items__test__code")
    inlines = [LabOrderItemInline]
