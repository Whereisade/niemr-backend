from rest_framework import serializers
from .models import VitalSign
from .enums import SeverityFlag

class VitalSignSerializer(serializers.ModelSerializer):
    class Meta:
        model = VitalSign
        fields = [
            "id","patient","facility","recorded_by","measured_at",
            "systolic","diastolic","heart_rate","temp_c","resp_rate","spo2",
            "weight_kg","height_cm","bmi",
            "bp_flag","temp_flag","spo2_flag","overall",
            "notes","created_at",
        ]
        read_only_fields = ["facility","recorded_by","bmi","bp_flag","temp_flag","spo2_flag","overall","created_at"]

    def create(self, validated):
        # recorded_by comes from request user
        validated["recorded_by"] = self.context["request"].user
        return super().create(validated)

class VitalSignListSerializer(serializers.ModelSerializer):
    class Meta:
        model = VitalSign
        fields = [
            "id","patient","measured_at","systolic","diastolic","heart_rate",
            "temp_c","resp_rate","spo2","bmi","overall"
        ]

class VitalSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    green = serializers.IntegerField()
    yellow = serializers.IntegerField()
    red = serializers.IntegerField()
    latest_overall = serializers.CharField()
