from rest_framework import serializers
from .models import Notification, Preference
from .enums import Topic, Channel

class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id","user","facility","topic","title","body","data","is_read","read_at","created_at"]
        read_only_fields = ["user","facility","is_read","read_at","created_at"]

class PreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Preference
        fields = ["id","topic","channel","enabled"]
