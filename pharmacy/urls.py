from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DrugViewSet, StockViewSet, PrescriptionViewSet

router = DefaultRouter()
router.register("catalog", DrugViewSet, basename="drug")
router.register("stock", StockViewSet, basename="stock")
router.register("prescriptions", PrescriptionViewSet, basename="rx")

urlpatterns = [ path("", include(router.urls)) ]
