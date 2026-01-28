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


    def to_representation(self, obj):
        """Enrich notification text for dashboard/activity feeds.

        Older notifications were created with IDs (e.g. "Patient #12").
        We keep the stored data as-is, but render a more human-friendly title/body
        by resolving names from payload IDs when available.

        This is intentionally best-effort and lightweight (recent feeds are small).
        """
        rep = super().to_representation(obj)
        try:
            import re

            request = self.context.get("request")
            ctx_user = getattr(request, "user", None)
            # Fallback for places where the serializer is used without context
            # (e.g. custom actions returning NotificationSerializer(...).data)
            user = ctx_user or getattr(obj, "user", None)
            role = (getattr(user, "role", "") or "").upper()
            # (e.g. custom actions returning NotificationSerializer(...).data)
            user = ctx_user or getattr(obj, "user", None)
            role = (getattr(user, "role", "") or "").upper()
            # (e.g. custom actions returning NotificationSerializer(...).data)
            user = ctx_user or getattr(obj, "user", None)
            role = (getattr(user, "role", "") or "").upper()
            # (e.g. custom actions returning NotificationSerializer(...).data)
            user = ctx_user or getattr(obj, "user", None)
            role = (getattr(user, "role", "") or "").upper()

            # (e.g. custom actions returning NotificationSerializer(...).data)
            user = ctx_user or getattr(obj, "user", None)
            role = (getattr(user, "role", "") or "").upper()

            # Fallback for places where the serializer is used without context
            # (e.g. custom actions returning NotificationSerializer(...).data)
            user = ctx_user or getattr(obj, "user", None)
            role = (getattr(user, "role", "") or "").upper()
            # Fallback for places where the serializer is used without context
            # (e.g. custom actions returning NotificationSerializer(...).data)
            user = ctx_user or getattr(obj, "user", None)
            role = (getattr(user, "role", "") or "").upper()
            # Fallback for places where the serializer is used without context
            # (e.g. custom actions returning NotificationSerializer(...).data)
            user = ctx_user or getattr(obj, "user", None)
            role = (getattr(user, "role", "") or "").upper()
            # Fallback for places where the serializer is used without context
            # (e.g. custom actions returning NotificationSerializer(...).data)
            user = ctx_user or getattr(obj, "user", None)
            role = (getattr(user, "role", "") or "").upper()
            # Fallback for places where the serializer is used without context
            # (e.g. custom actions returning NotificationSerializer(...).data)
            user = ctx_user or getattr(obj, "user", None)
            role = (getattr(user, "role", "") or "").upper()


            topic = rep.get("topic")
            title = rep.get("title") or ""
            body = rep.get("body") or ""
            data = rep.get("data") or {}

            def summarize(items, limit=3):
                items = [str(x).strip() for x in (items or []) if x]
                if not items:
                    return ""
                head = items[:limit]
                tail = len(items) - len(head)
                s = ", ".join(head)
                if tail > 0:
                    s += f" (+{tail} more)"
                return s

            # Prefer payload IDs, but fall back to parsing legacy text.
            patient_id = data.get("patient_id") or data.get("patient")
            order_id = data.get("order_id") or data.get("lab_order_id")
            rx_id = data.get("prescription_id") or data.get("rx_id")

            if not patient_id:
                m = re.search(r"Patient\s*#\s*(\d+)", body) or re.search(r"Patient\s*#\s*(\d+)", title)
                if m:
                    try:
                        patient_id = int(m.group(1))
                    except Exception:
                        patient_id = m.group(1)

            if not order_id and topic in {Topic.LAB_RESULT_READY, Topic.LAB_RESULT_CRITICAL}:
                m = re.search(r"Lab\s*order\s*#\s*(\d+)", body, re.I) or re.search(
                    r"Lab\s*order\s*#\s*(\d+)", title, re.I
                )
                if m:
                    try:
                        order_id = int(m.group(1))
                    except Exception:
                        order_id = m.group(1)

            if not rx_id and topic in {Topic.PRESCRIPTION_READY, Topic.PRESCRIPTION_REFILL}:
                m = re.search(r"Prescription\s*#\s*(\d+)", body, re.I) or re.search(
                    r"Prescription\s*#\s*(\d+)", title, re.I
                )
                if m:
                    try:
                        rx_id = int(m.group(1))
                    except Exception:
                        rx_id = m.group(1)

            # Resolve patient name (best-effort)
            patient_name = data.get("patient_name")
            if not patient_name and patient_id:
                try:
                    from patients.models import Patient

                    p = (
                        Patient.objects.filter(id=patient_id)
                        .only("first_name", "middle_name", "last_name")
                        .first()
                    )
                    if p:
                        patient_name = getattr(p, "full_name", None) or " ".join(
                            [
                                x
                                for x in [
                                    getattr(p, "first_name", ""),
                                    getattr(p, "middle_name", ""),
                                    getattr(p, "last_name", ""),
                                ]
                                if x
                            ]
                        ).strip()
                except Exception:
                    patient_name = None

            if patient_id and not patient_name:
                patient_name = f"Patient #{patient_id}"

            # Replace legacy "Patient #ID" where possible.
            if patient_id and patient_name and patient_name != f"Patient #{patient_id}":
                body = re.sub(
                    rf"Patient\s*#\s*{re.escape(str(patient_id))}\b",
                    patient_name,
                    body,
                )
                title = re.sub(
                    rf"Patient\s*#\s*{re.escape(str(patient_id))}\b",
                    patient_name,
                    title,
                )


            # Replace legacy "Lab order #ID" / "Prescription #ID" in title/body so patients don't see internal IDs.
            def replace_legacy(pattern, replacement):
                nonlocal title, body
                try:
                    title = re.sub(pattern, replacement, title, flags=re.I)
                    body = re.sub(pattern, replacement, body, flags=re.I)
                except Exception:
                    pass

            if order_id:
                replace_legacy(rf"Lab\s*order\s*#\s*{re.escape(str(order_id))}\b", "Lab results")
            if rx_id:
                replace_legacy(rf"Prescription\s*#\s*{re.escape(str(rx_id))}\b", "Prescription")


            # Replace legacy "Lab order #ID" / "Prescription #ID" in title/body so patients don't see internal IDs.
            def replace_legacy(pattern, replacement):
                nonlocal title, body
                try:
                    title = re.sub(pattern, replacement, title, flags=re.I)
                    body = re.sub(pattern, replacement, body, flags=re.I)
                except Exception:
                    pass

            if order_id:
                replace_legacy(
                    rf"Lab\s*order\s*#\s*{re.escape(str(order_id))}\b",
                    "Lab results",
                )
            if rx_id:
                replace_legacy(
                    rf"Prescription\s*#\s*{re.escape(str(rx_id))}\b",
                    "Prescription",
                )

            # Enrich Lab notifications: add test names (and patient name for staff).
            if topic in {Topic.LAB_RESULT_READY, Topic.LAB_RESULT_CRITICAL}:
                tests_summary = data.get("tests_summary")
                if not tests_summary and order_id:
                    try:
                        from labs.models import LabOrder

                        order = (
                            LabOrder.objects.filter(id=order_id)
                            .select_related("patient")
                            .prefetch_related("items__test")
                            .first()
                        )
                        if order:
                            # refresh patient name from order.patient if needed
                            if getattr(order, "patient", None) and (
                                not patient_name or patient_name == f"Patient #{patient_id}"
                            ):
                                try:
                                    patient_name = getattr(order.patient, "full_name", None) or patient_name
                                except Exception:
                                    pass

                            names = []
                            for it in order.items.all():
                                nm = None
                                if getattr(it, "test_id", None) and getattr(it, "test", None):
                                    nm = getattr(it.test, "name", None) or getattr(it.test, "code", None)
                                nm = nm or getattr(it, "requested_name", None)
                                if nm:
                                    names.append(str(nm))
                            tests_summary = summarize(names)
                    except Exception:
                        tests_summary = ""

                if tests_summary:
                    if role == UserRole.PATIENT:
                        line = f"Tests: {tests_summary}"
                        if line not in body:
                            body = f"{line}\n{body}".strip()
                    else:
                        prefix = (
                            f"{patient_name} • {tests_summary}" if patient_name else tests_summary
                        )
                        if prefix and prefix not in body:
                            body = f"{prefix}\n{body}".strip()

            # Enrich Prescription notifications: add medication names (and patient name for staff).
            if topic in {Topic.PRESCRIPTION_READY, Topic.PRESCRIPTION_REFILL}:
                meds_summary = data.get("meds_summary")
                if (not meds_summary or not patient_name or patient_name == f"Patient #{patient_id}") and rx_id:
                    try:
                        from pharmacy.models import Prescription

                        rx = (
                            Prescription.objects.filter(id=rx_id)
                            .select_related("patient")
                            .prefetch_related("items__drug")
                            .first()
                        )
                        if rx:
                            if getattr(rx, "patient", None) and (
                                not patient_name or patient_name == f"Patient #{patient_id}"
                            ):
                                try:
                                    patient_name = getattr(rx.patient, "full_name", None) or patient_name
                                except Exception:
                                    pass

                            if not meds_summary:
                                names = []
                                for it in rx.items.all():
                                    nm = None
                                    if getattr(it, "drug_id", None) and getattr(it, "drug", None):
                                        nm = getattr(it.drug, "name", None)
                                    nm = nm or getattr(it, "drug_name", None)
                                    dose = getattr(it, "dose", None) or ""
                                    nm = (str(nm).strip() if nm else "")
                                    dose = str(dose).strip()
                                    if nm and dose:
                                        names.append(f"{nm} {dose}".strip())
                                    elif nm:
                                        names.append(nm)
                                meds_summary = summarize(names)
                    except Exception:
                        meds_summary = meds_summary or ""

                if meds_summary:
                    if role == UserRole.PATIENT:
                        line = f"Medications: {meds_summary}"
                        if line not in body:
                            body = f"{line}\n{body}".strip()
                    else:
                        prefix = f"{patient_name} • {meds_summary}" if patient_name else meds_summary
                        if prefix and prefix not in body:
                            body = f"{prefix}\n{body}".strip()
            rep["title"] = title
            rep["body"] = body
        except Exception:
            return rep

        return rep


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
        """Validate and normalize roles list.

        Notes:
        - If empty, the backend will default to facility staff roles.
        - If includes PATIENT, the announcement will also be fanned out to
          patient/guardian accounts linked to patients in the facility.
        """
        cleaned = [r for r in (roles or []) if r]
        return cleaned
