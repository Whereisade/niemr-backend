# facilities/admin.py
from django.contrib import admin, messages
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password, ValidationError as PWValidationError

from .models import Facility, Ward, Bed, Specialty, FacilityExtraDocument

# Try both import paths so we don't crash if project uses a different location
try:
    from accounts.enums import UserRole  # preferred
except Exception:
    try:
        from accounts.constants import UserRole  # fallback
    except Exception:
        UserRole = type("UserRole", (), {"SUPER_ADMIN": "SUPER_ADMIN"})

class FacilityAdminForm(forms.ModelForm):
    """
    Extends the Facility admin form with optional fields to create
    the first Super Admin (with password).
    """
    create_super_admin = forms.BooleanField(
        required=False,
        initial=True,
        help_text="If checked, create/attach a SUPER_ADMIN for this facility."
    )
    admin_email = forms.EmailField(required=False, help_text="Email for the Super Admin to log in.")
    admin_password1 = forms.CharField(required=False, widget=forms.PasswordInput, label="Password")
    admin_password2 = forms.CharField(required=False, widget=forms.PasswordInput, label="Confirm password")
    admin_first_name = forms.CharField(required=False, label="First name")
    admin_last_name = forms.CharField(required=False, label="Last name")
    admin_phone = forms.CharField(required=False, label="Phone")

    class Meta:
        model = Facility
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("create_super_admin"):
            required_fields = ["admin_email", "admin_password1", "admin_password2", "admin_first_name", "admin_last_name"]
            missing = [f for f in required_fields if not cleaned.get(f)]
            if missing:
                raise forms.ValidationError({f: "This field is required." for f in missing})

            p1 = cleaned.get("admin_password1")
            p2 = cleaned.get("admin_password2")
            if p1 != p2:
                raise forms.ValidationError({"admin_password2": "Passwords do not match."})

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

    def _existing_model_fields(self):
        """
        Return a set of field names that actually exist on Facility to avoid fieldset crashes.
        """
        return {f.name for f in Facility._meta.get_fields() if getattr(f, "editable", False) or f.many_to_many}

    def get_fieldsets(self, request, obj=None):
        # Candidate facility fields (we'll filter by model reality)
        candidates = (
            "facility_type", "name", "controlled_by",
            "country", "state", "lga", "city", "address",
            "email", "registration_number", "phone",
            "nhis_approved", "nhis_number",
            "total_bed_capacity", "specialties",
            "nhis_certificate", "md_practice_license", "state_registration_cert",
            "is_active", "logo",
        )
        existing = self._existing_model_fields()
        main_fields = tuple([f for f in candidates if f in existing])

        fieldsets = [
            (None, {"fields": main_fields}),
            (
                "Create first Super Admin (optional)",
                {
                    "classes": ("collapse",),
                    "fields": (
                        "create_super_admin",
                        "admin_email", "admin_password1", "admin_password2",
                        "admin_first_name", "admin_last_name", "admin_phone",
                    ),
                    "description": (
                        "Create or attach the first SUPER_ADMIN for this facility. "
                        "If an account with this email already exists, it will be linked and promoted. "
                        "If a new password is provided for an existing user, it will be updated."
                    ),
                },
            ),
        ]
        return fieldsets

    def save_model(self, request, obj: Facility, form, change):
        """
        Save the facility first, then optionally create/attach the SUPER_ADMIN.
        """
        super().save_model(request, obj, form, change)

        if not form.cleaned_data.get("create_super_admin"):
            return

        User = get_user_model()

        admin_email = (form.cleaned_data.get("admin_email") or "").strip()
        admin_first_name = (form.cleaned_data.get("admin_first_name") or "").strip()
        admin_last_name = (form.cleaned_data.get("admin_last_name") or "").strip()
        admin_phone = (form.cleaned_data.get("admin_phone") or "").strip()
        admin_password = form.cleaned_data.get("admin_password1") or ""

        if not admin_email:
            messages.warning(request, "Super Admin not created: email missing.")
            return

        # Case-insensitive lookup WITHOUT using get_or_create with lookups
        qs = User._default_manager.filter(email__iexact=admin_email)
        created = False
        if qs.exists():
            user = qs.first()
        else:
            user = User(email=admin_email, first_name=admin_first_name, last_name=admin_last_name, is_active=True)
            created = True

        # Set password only if provided, so we don't accidentally blank it
        if admin_password:
            user.set_password(admin_password)

        # Update fields if provided
        if admin_first_name:
            user.first_name = admin_first_name
        if admin_last_name:
            user.last_name = admin_last_name
        if hasattr(user, "phone") and admin_phone:
            user.phone = admin_phone

        # Attach to facility & elevate role
        try:
            user.role = getattr(UserRole, "SUPER_ADMIN", "SUPER_ADMIN")
        except Exception:
            user.role = "SUPER_ADMIN"

        if hasattr(user, "facility"):
            user.facility = obj

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
