from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import FacilityViewSet, FacilityAdminRegisterView

router = DefaultRouter()
router.register("", FacilityViewSet, basename="facility")

urlpatterns = [
    path("register-admin/", FacilityAdminRegisterView.as_view(), name="facility-admin-register"),
    path("", include(router.urls)),
]
