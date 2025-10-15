from django.contrib import admin
from .models import Service, Price, Charge, Payment, PaymentAllocation

@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("code","name","default_price","is_active")
    search_fields = ("code","name")

@admin.register(Price)
class PriceAdmin(admin.ModelAdmin):
    list_display = ("facility","service","amount","currency")
    list_filter = ("facility",)

class AllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id","patient","facility","amount","method","reference","received_by","received_at")
    list_filter = ("facility","method")
    inlines = [AllocationInline]

@admin.register(Charge)
class ChargeAdmin(admin.ModelAdmin):
    list_display = ("id","patient","facility","service","qty","unit_price","amount","status","created_at")
    list_filter = ("facility","status")
    search_fields = ("service__name","service__code","description")
