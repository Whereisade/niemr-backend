from django.contrib import admin
from .models import Notification, Preference

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id","user","facility","topic","title","is_read","created_at")
    list_filter = ("topic","is_read","facility")
    search_fields = ("title","body","data")

@admin.register(Preference)
class PreferenceAdmin(admin.ModelAdmin):
    list_display = ("user","topic","channel","enabled")
    list_filter = ("topic","channel","enabled")
