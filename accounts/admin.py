from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email","role","is_active","is_staff","email_verified","last_login")
    search_fields = ("email","first_name","last_name")
    ordering = ("-date_joined",)
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Niemr Fields", {"fields": ("role","email_verified")}),
    )
