from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ServiceViewSet, PriceViewSet, ChargeViewSet, PaymentViewSet

router = DefaultRouter()
router.register("services", ServiceViewSet, basename="service")
router.register("prices", PriceViewSet, basename="price")
router.register("charges", ChargeViewSet, basename="charge")
router.register("payments", PaymentViewSet, basename="payment")

urlpatterns = [ path("", include(router.urls)) ]
