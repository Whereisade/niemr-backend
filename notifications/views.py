from django.utils.dateparse import parse_datetime
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import Notification, Preference
from .serializers import NotificationSerializer, PreferenceSerializer

class NotificationViewSet(viewsets.GenericViewSet,
                          mixins.ListModelMixin,
                          mixins.RetrieveModelMixin):
    serializer_class = NotificationSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        q = Notification.objects.filter(user=self.request.user)
        # filters: read, topic, since
        read = self.request.query_params.get("read")
        topic = self.request.query_params.get("topic")
        since = self.request.query_params.get("since")
        if read is not None:
            q = q.filter(is_read=(read.lower() == "true"))
        if topic:
            q = q.filter(topic=topic)
        if since:
            q = q.filter(created_at__gte=parse_datetime(since) or since)
        return q

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        n = self.get_object()
        if n.user_id != request.user.id:
            return Response({"detail":"Not allowed"}, status=403)
        n.mark_read()
        return Response({"ok": True})

    @action(detail=False, methods=["post"])
    def read_all(self, request):
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({"ok": True})

class PreferenceViewSet(viewsets.GenericViewSet,
                        mixins.ListModelMixin,
                        mixins.CreateModelMixin,
                        mixins.UpdateModelMixin):
    serializer_class = PreferenceSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Preference.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
