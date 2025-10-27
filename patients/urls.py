from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PatientViewSet, self_register,  DependentViewSet

router = DefaultRouter()
router.register("", PatientViewSet, basename="patient"),
router.register(r"dependents", DependentViewSet, basename="patient-dependents")

urlpatterns = [
    path("self-register/", self_register),
    path("", include(router.urls)),
]
