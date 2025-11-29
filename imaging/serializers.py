from django.db import transaction
from rest_framework import serializers
from .models import ImagingProcedure, ImagingRequest, ImagingReport, ImagingAsset
from .enums import RequestStatus


class ImagingProcedureSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImagingProcedure
        fields = ["id", "code", "name", "modality", "price", "is_active"]


class ImagingRequestCreateSerializer(serializers.ModelSerializer):
    # Optional: allow frontend to send a human code instead of numeric PK
    procedure_code = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=False,
    )

    class Meta:
        model = ImagingRequest
        fields = [
            "id",
            "patient",
            "procedure",        # FK, can be sent as ID
            "procedure_code",   # or this as a code, e.g. "CXR"
            "priority",
            "indication",
            "encounter_id",
            "external_center_name",
            "scheduled_for",
        ]
        extra_kwargs = {
            "procedure": {"required": False, "allow_null": True},
        }

    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]
        user = request.user

        # Resolve procedure either from FK or from code
        procedure = validated_data.get("procedure")
        procedure_code = validated_data.pop("procedure_code", None)

        if procedure is None:
            if procedure_code:
                try:
                    procedure = ImagingProcedure.objects.get(
                        code=procedure_code,
                        is_active=True,
                    )
                except ImagingProcedure.DoesNotExist:
                    raise serializers.ValidationError(
                        {
                            "procedure": (
                                f"Unknown or inactive procedure_code: "
                                f"{procedure_code}"
                            )
                        }
                    )
            else:
                raise serializers.ValidationError(
                    {
                        "procedure": (
                            "This field is required "
                            "(send procedure id or procedure_code)."
                        )
                    }
                )

        patient = validated_data["patient"]

        # Create the request and attach facility / requested_by
        req = ImagingRequest.objects.create(
            patient=patient,
            facility=(
                user.facility
                if getattr(user, "facility_id", None)
                else patient.facility
            ),
            requested_by=user,
            procedure=procedure,
            priority=validated_data.get("priority") or None,
            indication=validated_data.get("indication", "") or "",
            scheduled_for=validated_data.get("scheduled_for"),
            encounter_id=validated_data.get("encounter_id"),
            external_center_name=validated_data.get(
                "external_center_name", ""
            )
            or "",
        )

        # If we already have a scheduled time, mark as SCHEDULED
        if req.scheduled_for:
            req.status = RequestStatus.SCHEDULED
            req.save(update_fields=["status", "scheduled_for"])

        return req


class ImagingAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImagingAsset
        fields = ["id", "kind", "file", "uploaded_at"]


class ImagingReportSerializer(serializers.ModelSerializer):
    assets = ImagingAssetSerializer(many=True, read_only=True)

    class Meta:
        model = ImagingReport
        fields = [
            "id",
            "request",
            "reported_by",
            "findings",
            "impression",
            "reported_at",
            "assets",
        ]
        read_only_fields = ["request", "reported_by", "reported_at", "assets"]


class ImagingRequestReadSerializer(serializers.ModelSerializer):
    procedure = ImagingProcedureSerializer()
    report = ImagingReportSerializer(read_only=True)

    class Meta:
        model = ImagingRequest
        fields = [
            "id",
            "patient",
            "facility",
            "requested_by",
            "procedure",
            "priority",
            "status",
            "indication",
            "requested_at",
            "scheduled_for",
            "encounter_id",
            "external_center_name",
            "report",
        ]
