from django.utils.dateparse import parse_datetime
from django.db.models import Q
from rest_framework import viewsets, mixins
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import AuditLog
from .serializers import AuditLogSerializer
from .permissions import IsAdmin

class AuditLogViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin):
    serializer_class = AuditLogSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_queryset(self):
        q = AuditLog.objects.all()

        # filters
        actor = self.request.query_params.get("actor")         # user id
        verb  = self.request.query_params.get("verb")
        model = self.request.query_params.get("model")         # contenttype.model
        target_id = self.request.query_params.get("target_id")
        s = self.request.query_params.get("s")                 # message / email
        start = self.request.query_params.get("start")
        end   = self.request.query_params.get("end")

        if actor: q = q.filter(actor_id=actor)
        if verb: q = q.filter(verb=verb)
        if model: q = q.filter(target_ct__model=model.lower())
        if target_id: q = q.filter(target_id=str(target_id))
        if s: q = q.filter(Q(message__icontains=s) | Q(actor_email__icontains=s))
        if start: q = q.filter(created_at__gte=parse_datetime(start) or start)
        if end: q = q.filter(created_at__lte=parse_datetime(end) or end)

        return q.order_by("-created_at","-id")
