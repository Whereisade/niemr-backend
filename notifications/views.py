from django.db.models import Q
from django.utils import timezone

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication
from accounts.enums import UserRole

from .enums import Channel, Topic, Priority
from .models import Notification, Preference, Reminder, FacilityAnnouncement
from .serializers import (
    NotificationSerializer,
    PreferenceSerializer,
    PreferenceBulkItemSerializer,
    ReminderSerializer,
    FacilityAnnouncementSerializer,
    FacilityAnnouncementCreateSerializer,
)

from .permissions import CanBroadcastFacilityAnnouncements
from .services.notify import notify_facility_roles, notify_facility_patients, facility_staff_roles


class StandardPagination(PageNumberPagination):
    """Local pagination so we don't change global API behaviour."""

    page_size = 20
    page_size_query_param = "limit"
    page_query_param = "page"
    max_page_size = 100


def _parse_bool(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _parse_since(value: str):
    """Accept ISO datetime or small shortcuts like 7d / 24h / 30m."""
    if not value:
        return None
    value = str(value).strip()
    try:
        dt = timezone.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt
    except Exception:
        pass

    try:
        unit = value[-1].lower()
        num = int(value[:-1])
        if unit == "d":
            return timezone.now() - timezone.timedelta(days=num)
        if unit == "h":
            return timezone.now() - timezone.timedelta(hours=num)
        if unit == "m":
            return timezone.now() - timezone.timedelta(minutes=num)
    except Exception:
        return None
    return None


class NotificationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = NotificationSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_queryset(self):
        qs = Notification.objects.filter(user=self.request.user).select_related("facility")

        read = _parse_bool(self.request.query_params.get("read"))
        if read is not None:
            qs = qs.filter(is_read=read)

        archived = _parse_bool(self.request.query_params.get("archived"))
        if archived is not None:
            # Frontend uses this as a toggle. When true, show archived only.
            qs = qs.filter(archived=archived)
        else:
            # default: hide archived
            qs = qs.filter(archived=False)

        topic = self.request.query_params.get("topic")
        if topic:
            qs = qs.filter(topic=topic)

        priority = self.request.query_params.get("priority")
        if priority:
            qs = qs.filter(priority=priority)

        since = _parse_since(self.request.query_params.get("since"))
        if since:
            qs = qs.filter(created_at__gte=since)

        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(body__icontains=search))

        group_key = self.request.query_params.get("group_key")
        if group_key:
            qs = qs.filter(group_key=group_key)

        include_expired = _parse_bool(self.request.query_params.get("include_expired"))
        if not include_expired:
            # Hide expired notifications by default.
            qs = qs.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))

        return qs

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        obj = self.get_object()
        obj.mark_read()
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"])
    def unread(self, request, pk=None):
        obj = self.get_object()
        obj.mark_unread()
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        obj = self.get_object()
        obj.archive()
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"])
    def unarchive(self, request, pk=None):
        obj = self.get_object()
        obj.unarchive()
        return Response(self.get_serializer(obj).data)

    @action(detail=False, methods=["post"])
    def read_all(self, request):
        now = timezone.now()
        updated = (
            Notification.objects.filter(user=request.user, archived=False, is_read=False)
            .update(is_read=True, read_at=now)
        )
        return Response({"updated": updated})

    @action(detail=False, methods=["post"])
    def archive_all_read(self, request):
        now = timezone.now()
        updated = (
            Notification.objects.filter(user=request.user, archived=False, is_read=True)
            .update(archived=True, archived_at=now)
        )
        return Response({"updated": updated})

    @action(detail=False, methods=["post"])
    def batch_read(self, request):
        ids = request.data.get("ids") or []
        if not isinstance(ids, list):
            return Response({"detail": "ids must be a list"}, status=400)
        now = timezone.now()
        updated = (
            Notification.objects.filter(user=request.user, id__in=ids)
            .update(is_read=True, read_at=now)
        )
        return Response({"updated": updated})

    @action(detail=False, methods=["post"])
    def batch_archive(self, request):
        ids = request.data.get("ids") or []
        if not isinstance(ids, list):
            return Response({"detail": "ids must be a list"}, status=400)
        now = timezone.now()
        updated = (
            Notification.objects.filter(user=request.user, id__in=ids)
            .update(archived=True, archived_at=now)
        )
        return Response({"updated": updated})

    @action(detail=False, methods=["post"])
    def batch_delete(self, request):
        ids = request.data.get("ids") or []
        if not isinstance(ids, list):
            return Response({"detail": "ids must be a list"}, status=400)
        deleted, _ = Notification.objects.filter(user=request.user, id__in=ids).delete()
        return Response({"deleted": deleted})

    @action(detail=False, methods=["get"])
    def unread_count(self, request):
        base = Notification.objects.filter(user=request.user, archived=False)
        unread = base.filter(is_read=False).count()
        urgent = base.filter(is_read=False, priority=Priority.URGENT).count()
        return Response({"count": unread, "urgent_count": urgent})

    @action(detail=False, methods=["get"])
    def recent(self, request):
        try:
            limit = int(request.query_params.get("limit") or 10)
        except Exception:
            limit = 10
        limit = max(1, min(limit, 50))

        qs = Notification.objects.filter(user=request.user, archived=False).order_by("-created_at", "-id")
        items = list(qs[:limit])
        unread = qs.filter(is_read=False).count()
        return Response({
            "items": self.get_serializer(items, many=True).data,
            "total_unread": unread,
        })

    @action(detail=False, methods=["get"])
    def stats(self, request):
        base = Notification.objects.filter(user=request.user)
        total = base.count()
        unread = base.filter(is_read=False, archived=False).count()
        read = base.filter(is_read=True, archived=False).count()
        archived = base.filter(archived=True).count()
        urgent = base.filter(is_read=False, archived=False, priority=Priority.URGENT).count()
        return Response({
            "total": total,
            "unread": unread,
            "read": read,
            "archived": archived,
            "urgent": urgent,
        })

    @action(detail=False, methods=["get"])
    def topics(self, request):
        return Response([c for c, _ in Topic.choices])

    @action(detail=False, methods=["get"])
    def priorities(self, request):
        return Response([c for c, _ in Priority.choices])


class PreferenceViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = PreferenceSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Preference.objects.filter(user=self.request.user)

    def _default_enabled(self, topic: str, channel: str) -> bool:
        # Default behaviour: IN_APP on; EMAIL off.
        if channel == Channel.IN_APP:
            return True
        return False

    @action(detail=False, methods=["get"])
    def all_options(self, request):
        existing = {
            (p.topic, p.channel): p.enabled
            for p in Preference.objects.filter(user=request.user)
        }

        out = []
        for topic, _ in Topic.choices:
            for channel, _ in Channel.choices:
                enabled = existing.get((topic, channel))
                if enabled is None:
                    enabled = self._default_enabled(topic, channel)
                    source = "default"
                else:
                    source = "explicit"
                out.append({
                    "topic": topic,
                    "channel": channel,
                    "enabled": bool(enabled),
                    "source": source,
                })
        topics = [{"value": c, "label": label} for c, label in Topic.choices]
        channels = [{"value": c, "label": label} for c, label in Channel.choices]
        # Keep backward compatibility: frontend may expect `preferences`.
        return Response({
            "items": out,
            "preferences": out,
            "topics": topics,
            "channels": channels,
        })

    @action(detail=False, methods=["post"])
    def bulk_update(self, request):
        items = request.data.get("items")
        if not isinstance(items, list):
            return Response({"detail": "items must be a list"}, status=400)

        ser = PreferenceBulkItemSerializer(data=items, many=True)
        ser.is_valid(raise_exception=True)

        for row in ser.validated_data:
            Preference.objects.update_or_create(
                user=request.user,
                topic=row["topic"],
                channel=row["channel"],
                defaults={"enabled": row["enabled"]},
            )

        return self.all_options(request)

    @action(detail=False, methods=["post"])
    def enable_all(self, request):
        """Enable all topics for a channel (defaults to all topics)."""
        channel = request.data.get("channel")
        if channel not in dict(Channel.choices):
            return Response({"detail": "Invalid channel"}, status=400)
        topics = request.data.get("topics")
        if topics is None:
            topics = [c for c, _ in Topic.choices]
        if not isinstance(topics, list):
            return Response({"detail": "topics must be a list"}, status=400)
        for t in topics:
            if t not in dict(Topic.choices):
                continue
            Preference.objects.update_or_create(
                user=request.user,
                topic=t,
                channel=channel,
                defaults={"enabled": True},
            )
        return self.all_options(request)

    @action(detail=False, methods=["post"])
    def disable_all(self, request):
        """Disable all topics for a channel (defaults to all topics)."""
        channel = request.data.get("channel")
        if channel not in dict(Channel.choices):
            return Response({"detail": "Invalid channel"}, status=400)
        topics = request.data.get("topics")
        if topics is None:
            topics = [c for c, _ in Topic.choices]
        if not isinstance(topics, list):
            return Response({"detail": "topics must be a list"}, status=400)
        for t in topics:
            if t not in dict(Topic.choices):
                continue
            Preference.objects.update_or_create(
                user=request.user,
                topic=t,
                channel=channel,
                defaults={"enabled": False},
            )
        return self.all_options(request)


class FacilityAnnouncementViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Facility broadcast announcements.

    Create: creates a canonical announcement record and fans out per-user
    notifications to the chosen roles (role-scoped channels).
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "deactivate", "activate"}:
            return [IsAuthenticated(), CanBroadcastFacilityAnnouncements()]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action == "create":
            return FacilityAnnouncementCreateSerializer
        return FacilityAnnouncementSerializer

    def get_queryset(self):
        user = self.request.user
        facility_id = getattr(user, "facility_id", None)
        if not facility_id:
            return FacilityAnnouncement.objects.none()
        return (
            FacilityAnnouncement.objects.filter(facility_id=facility_id)
            .select_related("facility", "created_by")
            .order_by("-created_at", "-id")
        )

    def list(self, request, *args, **kwargs):
        user = request.user
        role = (getattr(user, "role", "") or "").upper()
        user_id = getattr(user, "id", None)

        qs = self.get_queryset()

        active = _parse_bool(request.query_params.get("active"))
        if active is None:
            active = True
        if active is not None:
            qs = qs.filter(is_active=active)

        current_only = _parse_bool(request.query_params.get("current"))
        if current_only is None:
            current_only = True

        # Role scoping: announcements that target specific roles should not be visible
        # to other roles UNLESS:
        # 1. The user is the creator of the announcement (can always see own announcements)
        # 2. The user is SUPER_ADMIN or ADMIN (can see all for audit/management)
        is_admin = role in {UserRole.SUPER_ADMIN, UserRole.ADMIN}
        
        items = []
        for ann in qs:
            # Always show to creator
            if getattr(ann, "created_by_id", None) == user_id:
                if current_only and not ann.is_current:
                    continue
                items.append(ann)
                continue
            
            # Always show to admins
            if is_admin:
                if current_only and not ann.is_current:
                    continue
                items.append(ann)
                continue
            
            # For other users, apply role scoping
            roles = list(getattr(ann, "audience_roles", []) or [])
            if roles and role not in roles:
                continue
            if current_only and not ann.is_current:
                continue
            items.append(ann)

        page = self.paginate_queryset(items)
        if page is not None:
            return self.get_paginated_response(FacilityAnnouncementSerializer(page, many=True).data)
        return Response(FacilityAnnouncementSerializer(items, many=True).data)

    def perform_create(self, serializer):
        user = self.request.user
        facility_id = getattr(user, "facility_id", None)
        if not facility_id:
            raise PermissionDenied("User is not attached to a facility")

        ann = serializer.save(facility_id=facility_id, created_by=user)

        roles = serializer.validated_data.get("audience_roles") or facility_staff_roles()
        group_key = f"ANN:{ann.id}"
        data = {
            "kind": "FACILITY_ANNOUNCEMENT",
            "announcement_id": ann.id,
            "audience_roles": roles,
        }

        # Fan-out to staff roles and/or patients/guardians depending on audience_roles.
        sent_total = 0
        patient_included = UserRole.PATIENT in (roles or [])
        staff_roles = [r for r in (roles or []) if r != UserRole.PATIENT]

        if staff_roles:
            sent_total += int(
                notify_facility_roles(
                    facility_id=facility_id,
                    roles=staff_roles,
                    topic=ann.topic,
                    title=ann.title,
                    body=ann.body or "",
                    data=data,
                    priority=ann.priority,
                    action_url=ann.action_url or "",
                    group_key=group_key,
                )
                or 0
            )
        else:
            # If roles only included PATIENT, don't accidentally default to staff.
            if not patient_included:
                sent_total += int(
                    notify_facility_roles(
                        facility_id=facility_id,
                        roles=None,
                        topic=ann.topic,
                        title=ann.title,
                        body=ann.body or "",
                        data=data,
                        priority=ann.priority,
                        action_url=ann.action_url or "",
                        group_key=group_key,
                    )
                    or 0
                )

        if patient_included:
            sent_total += int(
                notify_facility_patients(
                    facility_id=facility_id,
                    topic=ann.topic,
                    title=ann.title,
                    body=ann.body or "",
                    data=data,
                    priority=ann.priority,
                    action_url=ann.action_url or "",
                    group_key=group_key,
                )
                or 0
            )

        ann.sent_count = int(sent_total or 0)
        ann.save(update_fields=["sent_count", "updated_at"])
        return ann

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ann = self.perform_create(ser)
        return Response(FacilityAnnouncementSerializer(ann).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        ann = self.get_object()
        ann.is_active = False
        ann.save(update_fields=["is_active", "updated_at"])
        return Response(FacilityAnnouncementSerializer(ann).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        ann = self.get_object()
        ann.is_active = True
        ann.save(update_fields=["is_active", "updated_at"])
        return Response(FacilityAnnouncementSerializer(ann).data)


class ReminderViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ReminderSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        role = (getattr(user, "role", "") or "").upper()

        qs = Reminder.objects.all().select_related("patient", "nurse")

        # Patient: only own + dependents
        if role == UserRole.PATIENT:
            # own patient profile
            patient_id = getattr(getattr(user, "patient_profile", None), "id", None)
            # dependents via guardian_user
            return qs.filter(Q(patient__user_id=user.id) | Q(patient__guardian_user_id=user.id)).distinct()

        # Facility staff: scope to their facility
        if getattr(user, "facility_id", None):
            qs = qs.filter(patient__facility_id=user.facility_id)
            # nurses see only what they created by default
            if role == UserRole.NURSE:
                qs = qs.filter(nurse=user)
            return qs

        # Independent providers: only their reminders
        return qs.filter(nurse=user)

    def perform_create(self, serializer):
        user = self.request.user
        role = (getattr(user, "role", "") or "").upper()

        # Only staff can create reminders
        if role == UserRole.PATIENT:
            raise PermissionDenied("Patients cannot create reminders")

        patient = serializer.validated_data.get("patient")
        if getattr(user, "facility_id", None) and getattr(patient, "facility_id", None) != user.facility_id:
            raise PermissionDenied("You can only create reminders for patients in your facility")

        serializer.save(nurse=user)

    @action(detail=False, methods=["get"])
    def due_now(self, request):
        now = timezone.now()
        qs = self.get_queryset().filter(status__in=[Reminder.Status.PENDING, Reminder.Status.SENT], reminder_time__lte=now)
        return Response(ReminderSerializer(qs.order_by("reminder_time")[:50], many=True).data)

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        rem = self.get_object()
        rem.status = Reminder.Status.ACKNOWLEDGED
        rem.acknowledged_at = timezone.now()
        rem.save(update_fields=["status", "acknowledged_at"])
        return Response(ReminderSerializer(rem).data)

    @action(detail=True, methods=["post"])
    def dismiss(self, request, pk=None):
        rem = self.get_object()
        rem.status = Reminder.Status.DISMISSED
        rem.dismissed_at = timezone.now()
        rem.save(update_fields=["status", "dismissed_at"])
        return Response(ReminderSerializer(rem).data)