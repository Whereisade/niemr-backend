from django.db.models import Q
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from rest_framework_simplejwt.authentication import JWTAuthentication

from audit.services import log_action
from facilities.models import Facility
from django.contrib.auth import get_user_model

from .permissions import IsAppSuperAdmin
from .serializers import (
    FacilityAdminListSerializer,
    FacilityAdminDetailSerializer,
    UserAdminSerializer,
)

User = get_user_model()


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "limit"
    max_page_size = 100


class FacilityAdminViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
):
    """Application-level facility administration."""
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsAppSuperAdmin]
    pagination_class = StandardPagination
    queryset = Facility.objects.all().order_by("-created_at", "-id")

    def get_serializer_class(self):
        if self.action in ("retrieve", "update", "partial_update"):
            return FacilityAdminDetailSerializer
        return FacilityAdminListSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        qp = self.request.query_params

        q = (qp.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(email__icontains=q)
                | Q(state__icontains=q)
                | Q(lga__icontains=q)
            )

        vis = qp.get("is_publicly_visible")
        if vis is not None:
            if str(vis).lower() in ("true", "1", "yes"):
                qs = qs.filter(is_publicly_visible=True)
            elif str(vis).lower() in ("false", "0", "no"):
                qs = qs.filter(is_publicly_visible=False)

        active = qp.get("is_active")
        if active is not None:
            if str(active).lower() in ("true", "1", "yes"):
                qs = qs.filter(is_active=True)
            elif str(active).lower() in ("false", "0", "no"):
                qs = qs.filter(is_active=False)

        return qs

    def partial_update(self, request, *args, **kwargs):
        """Allow toggling a small safe subset of fields."""
        allowed = {"is_active", "is_publicly_visible"}
        data = {k: request.data.get(k) for k in allowed if k in request.data}
        if not data:
            return Response(
                {"detail": "No updatable fields provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        resp = super().partial_update(request, *args, **kwargs)

        try:
            obj = self.get_object()
            log_action(obj=obj, title=f"Facility updated by system admin: {obj.name}", extra={"fields": list(data.keys())})
        except Exception:
            pass

        return resp

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        f = self.get_object()
        f.is_publicly_visible = True
        f.save(update_fields=["is_publicly_visible", "updated_at"])
        try:
            log_action(obj=f, title=f"Facility approved for public listing: {f.name}")
        except Exception:
            pass
        return Response({"id": f.id, "is_publicly_visible": f.is_publicly_visible})

    @action(detail=True, methods=["post"])
    def hide(self, request, pk=None):
        f = self.get_object()
        f.is_publicly_visible = False
        f.save(update_fields=["is_publicly_visible", "updated_at"])
        try:
            log_action(obj=f, title=f"Facility hidden from public listing: {f.name}")
        except Exception:
            pass
        return Response({"id": f.id, "is_publicly_visible": f.is_publicly_visible})

    @action(detail=True, methods=["get"], url_path="users")
    def users(self, request, pk=None):
        """
        Users linked to a facility (accounts.User.facility_id).
        Filters:
          - s: search email/first/last
          - role: exact role
          - is_active: true/false
        """
        f = self.get_object()
        qs = User.objects.filter(facility_id=f.id).select_related("facility").order_by("-date_joined", "-id")

        s = (request.query_params.get("s") or "").strip()
        if s:
            qs = qs.filter(
                Q(email__icontains=s)
                | Q(first_name__icontains=s)
                | Q(last_name__icontains=s)
            )

        role = (request.query_params.get("role") or "").strip()
        if role:
            qs = qs.filter(role=role)

        is_active = request.query_params.get("is_active")
        if is_active is not None:
            if str(is_active).lower() in ("true", "1", "yes"):
                qs = qs.filter(is_active=True)
            elif str(is_active).lower() in ("false", "0", "no"):
                qs = qs.filter(is_active=False)

        page = self.paginate_queryset(qs)
        ser = UserAdminSerializer(page, many=True)
        return self.get_paginated_response(ser.data)


class UserAdminViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
):
    """Application-level user management."""
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsAppSuperAdmin]
    pagination_class = StandardPagination
    queryset = User.objects.select_related("facility").all().order_by("-date_joined", "-id")
    serializer_class = UserAdminSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        qp = self.request.query_params

        q = (qp.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(email__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
            )

        role = (qp.get("role") or "").strip()
        if role:
            qs = qs.filter(role=role)

        facility = (qp.get("facility") or "").strip()
        if facility:
            if facility == "none":
                qs = qs.filter(facility__isnull=True)
            else:
                try:
                    fid = int(facility)
                except Exception:
                    qs = qs.none()
                else:
                    qs = qs.filter(facility_id=fid)

        is_active = qp.get("is_active")
        if is_active is not None and is_active != "":
            if str(is_active).lower() in ("true", "1", "yes"):
                qs = qs.filter(is_active=True)
            elif str(is_active).lower() in ("false", "0", "no"):
                qs = qs.filter(is_active=False)

        is_sacked = qp.get("is_sacked")
        if is_sacked is not None and is_sacked != "":
            if str(is_sacked).lower() in ("true", "1", "yes"):
                qs = qs.filter(is_sacked=True)
            elif str(is_sacked).lower() in ("false", "0", "no"):
                qs = qs.filter(is_sacked=False)

        return qs

    def partial_update(self, request, *args, **kwargs):
        """Only allow toggling is_active for now."""
        if "is_active" not in request.data:
            return Response({"detail": "Only 'is_active' can be updated here."}, status=status.HTTP_400_BAD_REQUEST)

        resp = super().partial_update(request, *args, **kwargs)

        try:
            obj = self.get_object()
            log_action(obj=obj, title=f"User updated by system admin: {obj.email}", extra={"fields": ["is_active"]})
        except Exception:
            pass

        return resp

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        u = self.get_object()
        u.is_active = False
        u.save(update_fields=["is_active"])
        try:
            log_action(obj=u, title=f"User deactivated by system admin: {u.email}")
        except Exception:
            pass
        return Response({"id": u.id, "is_active": u.is_active})

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        u = self.get_object()
        u.is_active = True
        u.save(update_fields=["is_active"])
        try:
            log_action(obj=u, title=f"User reactivated by system admin: {u.email}")
        except Exception:
            pass
        return Response({"id": u.id, "is_active": u.is_active})
