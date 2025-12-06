# reports/serializers.py
from rest_framework import serializers


class GenerateReportSerializer(serializers.Serializer):
    REPORT_TYPES = ("ENCOUNTER", "LAB", "IMAGING", "BILLING")

    report_type = serializers.ChoiceField(choices=REPORT_TYPES)
    ref_id = serializers.IntegerField()
    as_pdf = serializers.BooleanField(required=False, default=True)
    save_as_attachment = serializers.BooleanField(required=False, default=False)
