from django.utils.dateparse import parse_datetime
from django.db.models import Q
from rest_framework import viewsets, mixins
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import AuditLog
from .serializers import AuditLogSerializer
from .permissions import IsAdmin


class AuditLogPagination(PageNumberPagination):
    """Custom pagination for audit logs."""
    page_size = 20
    page_size_query_param = 'limit'
    max_page_size = 100


class AuditLogViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin):
    serializer_class = AuditLogSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsAdmin]
    pagination_class = AuditLogPagination

    def get_queryset(self):
        user = self.request.user
        
        from accounts.enums import UserRole
        
        # Get user's facility
        facility_id = getattr(user, 'facility_id', None)
        
        # DEBUG (remove after testing)
        print(f"🔍 DEBUG - User: {user.email}, Role: {user.role}, Facility ID: {facility_id}")
        
        # CRITICAL FIX: Distinguish between Application Super Admin and Facility Super Admin
        # Only application-level super admins (with no facility) see everything
        if user.role == UserRole.SUPER_ADMIN and facility_id is None:
            # Application/Platform Super Admin (no facility) - sees ALL logs
            q = AuditLog.objects.all()
            count = q.count()
            print(f"🔍 APPLICATION SUPER_ADMIN: returning ALL {count} logs across all facilities")
        elif not facility_id:
            # User has no facility - return nothing
            print(f"⚠️  WARNING - User has no facility_id! Returning empty queryset")
            return AuditLog.objects.none()
        else:
            # Everyone else (including Facility Super Admins) - facility-scoped
            # This includes:
            # - Facility SUPER_ADMIN (with facility_id) 
            # - Facility ADMIN
            # - Other facility roles
            q = AuditLog.objects.filter(actor__facility_id=facility_id)
            count = q.count()
            print(f"🔍 {user.role} (Facility {facility_id}): returning {count} logs")

        # Apply additional filters
        actor = self.request.query_params.get("actor")
        verb  = self.request.query_params.get("verb")
        model = self.request.query_params.get("model")
        target_id = self.request.query_params.get("target_id")
        s = self.request.query_params.get("s")
        start = self.request.query_params.get("start")
        end   = self.request.query_params.get("end")

        if actor: 
            q = q.filter(actor_id=actor)
        if verb: 
            q = q.filter(verb=verb)
        if model: 
            q = q.filter(target_ct__model=model.lower())
        if target_id: 
            q = q.filter(target_id=str(target_id))
        if s: 
            q = q.filter(
                Q(message__icontains=s) | 
                Q(actor_email__icontains=s) |
                Q(target_id__icontains=s)
            )
        if start: 
            q = q.filter(created_at__gte=parse_datetime(start) or start)
        if end: 
            q = q.filter(created_at__lte=parse_datetime(end) or end)

        return q.select_related('actor', 'target_ct').order_by("-created_at", "-id")