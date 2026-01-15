from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    PatientViewSet, self_register, DependentViewSet, 
    PatientDocumentViewSet, AllergyViewSet
)
from .views import (
    SystemHMOViewSet, FacilityHMOViewSet, PatientHMOApprovalViewSet
)

router = DefaultRouter()

router.register("documents", PatientDocumentViewSet, basename="patient-document")
router.register("dependents", DependentViewSet, basename="patient-dependents")
router.register("allergies", AllergyViewSet, basename="patient-allergies")
router.register("hmo/system", SystemHMOViewSet, basename="system-hmo")
router.register("hmo/facility", FacilityHMOViewSet, basename="facility-hmo")
router.register("hmo-approvals", PatientHMOApprovalViewSet, basename="patient-hmo-approval")
router.register("", PatientViewSet, basename="patient")

urlpatterns = [
    path("self-register/", self_register),
    path("", include(router.urls)),
]