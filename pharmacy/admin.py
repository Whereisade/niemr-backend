from django.contrib import admin
from .models import Drug, StockItem, StockTxn, Prescription, PrescriptionItem, DispenseEvent

@admin.register(Drug)
class DrugAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "strength", "form", "route", "qty_per_unit", "unit_price", "facility", "created_by", "is_active")
    search_fields = ("code", "name")
    list_filter = ("facility", "is_active", "created_by")
    raw_id_fields = ("facility", "created_by")

@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ("facility", "drug", "current_qty")
    list_filter = ("facility",)

@admin.register(StockTxn)
class StockTxnAdmin(admin.ModelAdmin):
    list_display = ("facility", "drug", "txn_type", "qty", "created_by", "created_at")
    list_filter = ("facility", "txn_type")

class RxItemInline(admin.TabularInline):
    model = PrescriptionItem
    extra = 0

@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "facility", "prescribed_by", "status", "created_at")
    list_filter = ("facility", "status")
    search_fields = ("patient__first_name", "patient__last_name", "items__drug__name", "items__drug__code")
    inlines = [RxItemInline]

@admin.register(DispenseEvent)
class DispenseEventAdmin(admin.ModelAdmin):
    list_display = ("prescription_item", "qty", "dispensed_by", "dispensed_at")