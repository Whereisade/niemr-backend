from django.contrib import admin, messages
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password, ValidationError as PWValidationError

from .models import Facility, Ward, Bed, Specialty, FacilityExtraDocument
from accounts.enums import UserRole  # your project already uses this in views

class FacilityAdminForm(forms.ModelForm):
    """
    Extends the Facility admin form with optional fields to create the first Super Admin.
    """
    create_super_admin = forms.BooleanField(
        required=False,
        initial=True,
        help_text="If checked, create/attach a SUPER_ADMIN for this facility."
    )
    admin_email = forms.EmailField(required=False, help_text="Email for the Super Admin to log in.")
    admin_password1 = forms.CharField(required=False, widget=forms.PasswordInput, label="Password")
    admin_password2 = forms.CharField(required=False, widget=forms.PasswordInput, label="Confirm password")
    admin_first_name = forms.CharField(required=False)
    admin_last_name = forms.CharField(required=False)
    admin_phone = forms.CharField(required=False)

    class Meta:
        model = Facility
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("create_super_admin"):
            # Require core fields
            required_fields = ["admin_email", "admin_password1", "admin_password2", "admin_first_name", "admin_last_name"]
            missing = [f for f in required_fields if not cleaned.get(f)]
            if missing:
                raise forms.ValidationError({f: "This field is required." for f in missing})

            p1 = cleaned.get("admin_password1")
            p2 = cleaned.get("admin_password2")
            if p1 != p2:
                raise forms.ValidationError({"admin_password2": "Passwords do not match."})

            # Validate password strength
            try:
                validate_password(p1)
            except PWValidationError as e:
                raise forms.ValidationError({"admin_password1": list(e.messages)})

        return cleaned


@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    form = FacilityAdminForm

    list_display = ("name", "facility_type", "country", "state", "lga", "email", "phone", "nhis_approved", "created_at")
    search_fields = ("name", "email", "registration_number", "state", "lga")
    list_filter = ("facility_type", "nhis_approved", "country", "state")

    fieldsets = (
        (None, {
            "fields": (
                "facility_type", "name", "controlled_by",
                "country", "state", "lga", "address",
                "email", "registration_number", "phone",
                "nhis_approved", "nhis_number",
                "total_bed_capacity", "specialties",
                "nhis_certificate", "md_practice_license", "state_registration_cert",
                "is_active",
            )
        }),
        ("Create first Super Admin (optional)", {
            "classes": ("collapse",),  # expand/collapse in admin UI
            "fields": (
                "create_super_admin",
                "admin_email", "admin_password1", "admin_password2",
                "admin_first_name", "admin_last_name", "admin_phone",
            ),
            "description": "Create or attach the first SUPER_ADMIN for this facility. "
                           "If an account with this email already exists, it will be linked to this facility and "
                           "promoted to SUPER_ADMIN. If you enter a new password for an existing user, it will be updated."
        }),
    )

    def save_model(self, request, obj: Facility, form, change):
        """
        Save the facility first, then optionally create/attach the SUPER_ADMIN.
        """
        super().save_model(request, obj, form, change)

        if not form.cleaned_data.get("create_super_admin"):
            return  # nothing else to do

        User = get_user_model()

        admin_email = form.cleaned_data.get("admin_email")
        admin_first_name = form.cleaned_data.get("admin_first_name") or ""
        admin_last_name = form.cleaned_data.get("admin_last_name") or ""
        admin_phone = form.cleaned_data.get("admin_phone") or ""
        admin_password = form.cleaned_data.get("admin_password1")

        user, created = User.objects.get_or_create(
            email__iexact=admin_email,
            defaults={
                "email": admin_email,
                "first_name": admin_first_name,
                "last_name": admin_last_name,
                "is_active": True,
            },
        )
        # get_or_create with email__iexact uses lookup; if not created, we must fetch again by email
        if not created and not hasattr(user, "id"):
            try:
                user = User.objects.get(email__iexact=admin_email)
            except User.DoesNotExist:
                # Fallback: create explicitly
                user = User(email=admin_email, first_name=admin_first_name, last_name=admin_last_name, is_active=True)
                created = True

        # Update (or set) fields
        # Only change password if a new one was supplied
        if admin_password:
            user.set_password(admin_password)

        # Update names/phone if provided (don’t overwrite with blanks)
        if admin_first_name:
            user.first_name = admin_first_name
        if admin_last_name:
            user.last_name = admin_last_name
        if hasattr(user, "phone") and admin_phone:
            user.phone = admin_phone

        # Attach to facility & elevate role
        user.facility = obj
        user.role = getattr(UserRole, "SUPER_ADMIN", "SUPER_ADMIN")
        user.is_active = True
        user.save()

        if created:
            messages.success(request, f"Super Admin '{user.email}' created and attached to {obj.name}.")
        else:
            messages.success(request, f"Super Admin '{user.email}' attached/updated for {obj.name}.")
        

admin.site.register(Ward)
admin.site.register(Bed)
admin.site.register(Specialty)
admin.site.register(FacilityExtraDocument)
