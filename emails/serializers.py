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
