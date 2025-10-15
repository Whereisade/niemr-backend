from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProcedureViewSet, ImagingRequestViewSet

router = DefaultRouter()
router.register("procedures", ProcedureViewSet, basename="procedure")
router.register("requests", ImagingRequestViewSet, basename="imaging-request")

urlpatterns = [
    path("", include(router.urls)),
]
