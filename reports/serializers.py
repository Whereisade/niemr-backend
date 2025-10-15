from rest_framework import serializers
from .models import ReportJob, ReportType

class GenerateRequestSerializer(serializers.Serializer):
    report_type = serializers.ChoiceField(choices=ReportType.choices)
    ref_id = serializers.IntegerField()
    as_pdf = serializers.BooleanField(required=False, default=True)
    save_as_attachment = serializers.BooleanField(required=False, default=False)

    # Only for billing
    start = serializers.DateTimeField(required=False, allow_null=True)
    end = serializers.DateTimeField(required=False, allow_null=True)
