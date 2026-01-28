from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views
from .facility_admin_views import (
    list_facility_staff,
    create_facility_staff,
    get_facility_staff,
    update_facility_staff,
    deactivate_facility_staff,
    reactivate_facility_staff,
)

urlpatterns = [
    # Auth endpoints
    path("register/", views.register),
    path("login/", views.login_password),
    path("google/", views.login_google),
    path("token/refresh/", TokenRefreshView.as_view()),
    path("me/", views.me),

    # Password reset
    path("password/reset/", views.password_reset_request),
    path("password/reset/confirm/", views.password_reset_confirm),
    path("password/change/", views.password_change),
    path("visibility/", views.visibility_settings, name="visibility-settings"),

    # Facility staff management (SUPER_ADMIN only)
    path("facility-staff/", list_facility_staff, name="facility-staff-list"),
    path("facility-staff/create/", create_facility_staff, name="facility-staff-create"),
    path("facility-staff/<int:pk>/", get_facility_staff, name="facility-staff-detail"),
    path("facility-staff/<int:pk>/update/", update_facility_staff, name="facility-staff-update"),
    path("facility-staff/<int:pk>/deactivate/", deactivate_facility_staff, name="facility-staff-deactivate"),
    path("facility-staff/<int:pk>/reactivate/", reactivate_facility_staff, name="facility-staff-reactivate"),
]