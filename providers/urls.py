from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProviderViewSet, self_register

router = DefaultRouter()
router.register("", ProviderViewSet, basename="provider")

urlpatterns = [
    path("self-register/", self_register),
    path("", include(router.urls)),
]
