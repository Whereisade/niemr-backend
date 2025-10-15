from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import LabTestViewSet, LabOrderViewSet

router = DefaultRouter()
router.register("catalog", LabTestViewSet, basename="labtest")
router.register("orders", LabOrderViewSet, basename="laborder")

urlpatterns = [
    path("", include(router.urls)),
]
