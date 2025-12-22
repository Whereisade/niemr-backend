from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import NotificationViewSet, PreferenceViewSet, ReminderViewSet, FacilityAnnouncementViewSet


router = DefaultRouter()


router.register(r"preferences", PreferenceViewSet, basename="notification-preferences")
router.register(r"announcements", FacilityAnnouncementViewSet, basename="facility-announcement")
router.register(r"reminders", ReminderViewSet, basename="notification-reminders")
router.register(r"", NotificationViewSet, basename="notification")


urlpatterns = [
    path("", include(router.urls)),
]
