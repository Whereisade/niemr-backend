from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    ProviderViewSet,
    self_register,               # keep your existing self-register FBV
    apply_to_facility,
    my_facility_applications,
    facility_provider_applications,
    facility_provider_application_decide,
)

router = DefaultRouter()
router.register(r"", ProviderViewSet, basename="provider")

urlpatterns = [
    path("self-register/", self_register, name="provider-self-register"),

    # Provider ↔ Facility applications
    path("apply-to-facility/", apply_to_facility, name="provider-apply-to-facility"),
    path("my-facility-applications/", my_facility_applications, name="provider-my-facility-applications"),
    path("facility/applications/", facility_provider_applications, name="facility-provider-applications"),
    path(
        "facility/applications/<int:pk>/approve/",
        facility_provider_application_decide,
        {"decision": "approve"},
        name="facility-provider-application-approve",
    ),
    path(
        "facility/applications/<int:pk>/reject/",
        facility_provider_application_decide,
        {"decision": "reject"},
        name="facility-provider-application-reject",
    ),

    path("", include(router.urls)),
]
