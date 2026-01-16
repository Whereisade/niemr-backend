from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    PatientViewSet, 
    self_register, 
    DependentViewSet, 
    PatientDocumentViewSet, 
    AllergyViewSet,
    SystemHMOViewSet, 
    FacilityHMOViewSet, 
    PatientHMOApprovalViewSet
)

router = DefaultRouter()

# Core patient endpoints
router.register("documents", PatientDocumentViewSet, basename="patient-document")
router.register("dependents", DependentViewSet, basename="patient-dependents")
router.register("allergies", AllergyViewSet, basename="patient-allergies")

# HMO endpoints
router.register("hmo/system", SystemHMOViewSet, basename="system-hmo")
router.register("hmo/facility", FacilityHMOViewSet, basename="facility-hmo")
router.register("hmo-approvals", PatientHMOApprovalViewSet, basename="patient-hmo-approval")

# Main patient endpoint (must be last due to empty prefix)
router.register("", PatientViewSet, basename="patient")

urlpatterns = [
    path("self-register/", self_register),
    path("", include(router.urls)),
]