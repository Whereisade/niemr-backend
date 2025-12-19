from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    PatientViewSet, self_register, DependentViewSet, 
    PatientDocumentViewSet, AllergyViewSet
)

router = DefaultRouter()

router.register("documents", PatientDocumentViewSet, basename="patient-document")
router.register("dependents", DependentViewSet, basename="patient-dependents")
router.register("allergies", AllergyViewSet, basename="patient-allergies")
router.register("", PatientViewSet, basename="patient")

urlpatterns = [
    path("self-register/", self_register),
    path("", include(router.urls)),
]