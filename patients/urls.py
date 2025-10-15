from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PatientViewSet, self_register

router = DefaultRouter()
router.register("", PatientViewSet, basename="patient")

urlpatterns = [
    path("self-register/", self_register),
    path("", include(router.urls)),
]
