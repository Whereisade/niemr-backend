from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NotificationViewSet, PreferenceViewSet
from .views import ReminderViewSet

router = DefaultRouter()
router.register('reminders', ReminderViewSet, basename="reminder")
router.register("items", NotificationViewSet, basename="notification")
router.register("prefs", PreferenceViewSet, basename="preference")

urlpatterns = [ path("", include(router.urls)) ]
