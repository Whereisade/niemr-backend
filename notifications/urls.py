from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NotificationViewSet, PreferenceViewSet

router = DefaultRouter()
router.register("items", NotificationViewSet, basename="notification")
router.register("prefs", PreferenceViewSet, basename="preference")

urlpatterns = [ path("", include(router.urls)) ]
