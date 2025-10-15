from django.contrib import admin
from .models import Appointment

@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("id","patient","facility","provider","appt_type","status","start_at","end_at","created_at")
    list_filter = ("facility","status","appt_type")
    search_fields = ("patient__first_name","patient__last_name","reason","notes")
