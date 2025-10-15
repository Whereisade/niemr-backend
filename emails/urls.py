from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import OutboxViewSet, TemplateViewSet, resend_webhook

router = DefaultRouter()
router.register("outbox", OutboxViewSet, basename="outbox")
router.register("templates", TemplateViewSet, basename="template")

urlpatterns = [
    path("", include(router.urls)),
    path("webhooks/resend/", resend_webhook),
]
