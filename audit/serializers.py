from rest_framework import serializers
from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    target_model = serializers.SerializerMethodField()
    target_display = serializers.SerializerMethodField()
    actor_display = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor",
            "actor_email",
            "actor_display",
            "ip_address",
            "user_agent",
            "verb",
            "message",
            "target_ct",
            "target_id",
            "target_model",
            "target_display",
            "changes",
            "extra",
            "created_at",
        ]

    def get_target_model(self, obj):
        return obj.target_ct.model

    def get_actor_display(self, obj):
        """Return a friendly display name for the actor."""
        if obj.actor:
            name_parts = []
            if getattr(obj.actor, "first_name", ""):
                name_parts.append(obj.actor.first_name)
            if getattr(obj.actor, "last_name", ""):
                name_parts.append(obj.actor.last_name)
            if name_parts:
                return " ".join(name_parts)
            return obj.actor_email or str(obj.actor)
        return obj.actor_email or "System"

    def get_target_display(self, obj):
        """
        Attempt to retrieve a human-readable name for the target object.
        Falls back to the target_id if no name can be determined.
        """
        model_name = obj.target_ct.model.lower()
        target_id = obj.target_id

        # Try to get the actual object and extract a meaningful name
        try:
            model_class = obj.target_ct.model_class()
            if model_class is None:
                return self._extract_name_from_changes(obj, model_name)

            instance = model_class.objects.filter(pk=target_id).first()
            if instance is None:
                # Object was deleted, try to get name from changes
                return self._extract_name_from_changes(obj, model_name)

            # Try common name fields in order of preference
            name_fields = [
                # Full name combinations
                ("first_name", "last_name"),
                # Single name fields
                "name",
                "title",
                "display_name",
                "full_name",
                # Identifiers
                "email",
                "hospital_number",
                "order_number",
                "code",
                "reference",
                # Description fields
                "subject",
                "description",
            ]

            for field in name_fields:
                if isinstance(field, tuple):
                    # Combine multiple fields (e.g., first_name + last_name)
                    parts = []
                    for f in field:
                        val = getattr(instance, f, None)
                        if val:
                            parts.append(str(val))
                    if parts:
                        return " ".join(parts)
                else:
                    val = getattr(instance, field, None)
                    if val:
                        return str(val)[:100]  # Truncate long values

            # Try __str__ method
            str_repr = str(instance)
            if str_repr and str_repr != f"{model_name} object ({target_id})":
                return str_repr[:100]

        except Exception:
            pass

        # Fallback: try to extract from changes
        return self._extract_name_from_changes(obj, model_name)

    def _extract_name_from_changes(self, obj, model_name):
        """
        Extract a meaningful name from the changes JSON field.
        Useful when the object has been deleted.
        """
        changes = obj.changes or {}

        # Look in 'after' first (for creates), then 'before' (for deletes/updates)
        for key in ["after", "before"]:
            data = changes.get(key, {})
            if not isinstance(data, dict):
                continue

            # Try common name fields
            name_fields = [
                ("first_name", "last_name"),
                "name",
                "title",
                "display_name",
                "email",
                "hospital_number",
                "code",
            ]

            for field in name_fields:
                if isinstance(field, tuple):
                    parts = []
                    for f in field:
                        val = data.get(f)
                        if val:
                            parts.append(str(val))
                    if parts:
                        return " ".join(parts)
                else:
                    val = data.get(field)
                    if val:
                        return str(val)[:100]

        # Final fallback: return model name + truncated ID
        short_id = str(obj.target_id)
        if len(short_id) > 8:
            short_id = short_id[:8] + "…"
        return f"{model_name.replace('_', ' ').title()} #{short_id}"