from django.utils import timezone
from rest_framework import serializers

from .enums import Channel, Topic, Priority
from .models import Notification, Preference, Reminder, FacilityAnnouncement

from accounts.enums import UserRole


class NotificationSerializer(serializers.ModelSerializer):
    time_ago = serializers.SerializerMethodField()
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "user",
            "facility",
            "topic",
            "priority",
            "title",
            "body",
            "data",
            "action_url",
            "group_key",
            "expires_at",
            "is_expired",
            "is_read",
            "read_at",
            "archived",
            "archived_at",
            "created_at",
            "updated_at",
            "time_ago",
        ]
        read_only_fields = [
            "user",
            "facility",
            "is_read",
            "read_at",
            "archived",
            "archived_at",
            "created_at",
            "updated_at",
            "time_ago",
            "is_expired",
        ]

    def get_time_ago(self, obj):
        # Keep frontend simple; still okay if frontend ignores this.
        try:
            delta = timezone.now() - obj.created_at
            seconds = int(delta.total_seconds())
            if seconds < 60:
                return f"{seconds}s"
            minutes = seconds // 60
            if minutes < 60:
                return f"{minutes}m"
            hours = minutes // 60
            if hours < 24:
                return f"{hours}h"
            days = hours // 24
            return f"{days}d"
        except Exception:
            return None

    def get_is_expired(self, obj):
        return obj.is_expired


class PreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Preference
        fields = ["id", "topic", "channel", "enabled"]


class PreferenceBulkItemSerializer(serializers.Serializer):
    topic = serializers.ChoiceField(choices=Topic.choices)
    channel = serializers.ChoiceField(choices=Channel.choices)
    enabled = serializers.BooleanField()


class ReminderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reminder
        fields = [
            "id",
            "patient",
            "nurse",
            "reminder_type",
            "message",
            "reminder_time",
            "status",
            "sent_at",
            "acknowledged_at",
            "dismissed_at",
            "created_at",
        ]
        read_only_fields = [
            "nurse",
            "status",
            "sent_at",
            "acknowledged_at",
            "dismissed_at",
            "created_at",
        ]


class FacilityAnnouncementSerializer(serializers.ModelSerializer):
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = FacilityAnnouncement
        fields = [
            "id",
            "facility",
            "created_by",
            "topic",
            "priority",
            "title",
            "body",
            "action_url",
            "audience_roles",
            "is_active",
            "starts_at",
            "ends_at",
            "sent_count",
            "created_at",
            "updated_at",
            "is_current",
        ]
        read_only_fields = [
            "facility",
            "created_by",
            "sent_count",
            "created_at",
            "updated_at",
            "is_current",
        ]

    def get_is_current(self, obj):
        try:
            return obj.is_current
        except Exception:
            return False


class FacilityAnnouncementCreateSerializer(serializers.ModelSerializer):
    """Create serializer with role validation."""

    audience_roles = serializers.ListField(
        child=serializers.ChoiceField(choices=UserRole.choices),
        required=False,
        allow_empty=True,
    )

    class Meta:
        model = FacilityAnnouncement
        fields = [
            "topic",
            "priority",
            "title",
            "body",
            "action_url",
            "audience_roles",
            "is_active",
            "starts_at",
            "ends_at",
        ]

    def validate_audience_roles(self, roles):
        # Exclude PATIENT for facility staff announcements.
        cleaned = [r for r in (roles or []) if r]
        if UserRole.PATIENT in cleaned:
            raise serializers.ValidationError("audience_roles cannot include PATIENT")
        return cleaned
