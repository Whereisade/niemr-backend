from rest_framework import serializers
from .models import AuditLog

class AuditLogSerializer(serializers.ModelSerializer):
    target_model = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id","actor","actor_email","ip_address","user_agent",
            "verb","message","target_ct","target_id","target_model",
            "changes","extra","created_at",
        ]

    def get_target_model(self, obj):
        return obj.target_ct.model
