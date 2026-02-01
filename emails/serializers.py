from rest_framework import serializers
from .models import Outbox, Template

class OutboxSerializer(serializers.ModelSerializer):
    class Meta:
        model = Outbox
        fields = "__all__"

class TemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Template
        fields = ["id","code","subject","html","text","created_at"]


class EnquirySerializer(serializers.Serializer):
    """Public website enquiry payload."""

    name = serializers.CharField(max_length=120)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=40, required=False, allow_blank=True)
    subject = serializers.CharField(max_length=140, required=False, allow_blank=True)
    message = serializers.CharField(max_length=4000)

    # Simple anti-spam honeypot (frontend should keep it empty)
    website = serializers.CharField(required=False, allow_blank=True, write_only=True)

    def validate(self, attrs):
        if attrs.get("website"):
            raise serializers.ValidationError("Invalid submission")
        return attrs
