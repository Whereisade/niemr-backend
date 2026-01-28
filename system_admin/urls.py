from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import FacilityAdminViewSet, UserAdminViewSet

router = DefaultRouter()
router.register(r"facilities", FacilityAdminViewSet, basename="system-admin-facilities")
router.register(r"users", UserAdminViewSet, basename="system-admin-users")

urlpatterns = [
    path("", include(router.urls)),
]
