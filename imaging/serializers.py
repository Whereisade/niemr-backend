from django.db import transaction
from rest_framework import serializers
from .models import ImagingProcedure, ImagingRequest, ImagingReport, ImagingAsset
from .enums import RequestStatus

class ImagingProcedureSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImagingProcedure
        fields = ["id","code","name","modality","price","is_active"]

class ImagingRequestCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImagingRequest
        fields = ["id","patient","procedure","priority","indication","encounter_id","external_center_name","scheduled_for"]

    def create(self, validated):
        u = self.context["request"].user
        req = ImagingRequest.objects.create(
            patient=validated["patient"],
            facility=u.facility if u.facility_id else validated["patient"].facility,
            requested_by=u,
            procedure=validated["procedure"],
            priority=validated.get("priority"),
            indication=validated.get("indication",""),
            scheduled_for=validated.get("scheduled_for"),
            encounter_id=validated.get("encounter_id"),
            external_center_name=validated.get("external_center_name",""),
        )
        if req.scheduled_for:
            req.status = RequestStatus.SCHEDULED
            req.save(update_fields=["status"])
        return req

class ImagingAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImagingAsset
        fields = ["id","kind","file","uploaded_at"]

class ImagingReportSerializer(serializers.ModelSerializer):
    assets = ImagingAssetSerializer(many=True, read_only=True)
    class Meta:
        model = ImagingReport
        fields = ["id","request","reported_by","findings","impression","reported_at","assets"]
        read_only_fields = ["request","reported_by","reported_at","assets"]

class ImagingRequestReadSerializer(serializers.ModelSerializer):
    procedure = ImagingProcedureSerializer()
    report = ImagingReportSerializer(read_only=True)
    class Meta:
        model = ImagingRequest
        fields = [
            "id","patient","facility","requested_by","procedure",
            "priority","status","indication","requested_at","scheduled_for",
            "encounter_id","external_center_name","report"
        ]
