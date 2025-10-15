from django.contrib import admin
from .models import VitalSign

@admin.register(VitalSign)
class VitalSignAdmin(admin.ModelAdmin):
    list_display = ("patient","facility","measured_at","systolic","diastolic","temp_c","spo2","overall","created_at")
    list_filter  = ("facility","overall")
    search_fields = ("patient__first_name","patient__last_name","patient__email")
