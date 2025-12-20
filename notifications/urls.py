# notifications/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NotificationViewSet, PreferenceViewSet, ReminderViewSet

app_name = "notifications"

router = DefaultRouter()
router.register(r"notifications", NotificationViewSet, basename="notification")
router.register(r"preferences", PreferenceViewSet, basename="preference")
router.register(r"reminders", ReminderViewSet, basename="reminder")

urlpatterns = [
    path("", include(router.urls)),
]

# Generated URL patterns:
#
# Notifications:
#   GET    /notifications/                    - List notifications (with filtering)
#   POST   /notifications/                    - Create notification
#   GET    /notifications/{id}/               - Retrieve notification
#   PUT    /notifications/{id}/               - Update notification
#   DELETE /notifications/{id}/               - Delete notification
#   POST   /notifications/{id}/read/          - Mark as read
#   POST   /notifications/{id}/unread/        - Mark as unread
#   POST   /notifications/{id}/archive/       - Archive notification
#   POST   /notifications/{id}/unarchive/     - Unarchive notification
#   POST   /notifications/read_all/           - Mark all as read
#   POST   /notifications/archive_all_read/   - Archive all read notifications
#   POST   /notifications/batch_read/         - Batch mark as read
#   POST   /notifications/batch_archive/      - Batch archive
#   POST   /notifications/batch_delete/       - Batch delete
#   GET    /notifications/stats/              - Get notification stats
#   GET    /notifications/unread_count/       - Get unread count (lightweight)
#   GET    /notifications/recent/             - Get recent notifications (for dropdown)
#   GET    /notifications/topics/             - Get available topics
#   GET    /notifications/priorities/         - Get available priorities
#
# Preferences:
#   GET    /preferences/                      - List user preferences
#   POST   /preferences/                      - Create preference
#   GET    /preferences/{id}/                 - Retrieve preference
#   PUT    /preferences/{id}/                 - Update preference
#   DELETE /preferences/{id}/                 - Delete preference
#   GET    /preferences/all_options/          - Get all topic/channel options
#   POST   /preferences/bulk_update/          - Bulk update preferences
#   POST   /preferences/enable_all/           - Enable all for a channel
#   POST   /preferences/disable_all/          - Disable all for a channel
#
# Reminders:
#   GET    /reminders/                        - List reminders (with filtering)
#   POST   /reminders/                        - Create reminder
#   GET    /reminders/{id}/                   - Retrieve reminder
#   PUT    /reminders/{id}/                   - Update reminder
#   DELETE /reminders/{id}/                   - Delete reminder
#   POST   /reminders/{id}/acknowledge/       - Acknowledge reminder
#   POST   /reminders/{id}/dismiss/           - Dismiss reminder
#   GET    /reminders/due_now/                - Get reminders due now
#   GET    /reminders/types/                  - Get available reminder types