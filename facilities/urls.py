from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import FacilityViewSet, FacilityAdminRegisterView, BedAssignmentViewSet

router = DefaultRouter()
router.register("bed-assignments", BedAssignmentViewSet, basename="bed-assignment")
router.register("", FacilityViewSet, basename="facility")

urlpatterns = [
    path("register-admin/", FacilityAdminRegisterView.as_view(), name="facility-register-admin"),
    path("", include(router.urls)),
]



