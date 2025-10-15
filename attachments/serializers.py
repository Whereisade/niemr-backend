from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers
from .models import File, AttachmentLink
from .enums import Visibility

MAX_SIZE_BYTES = 20 * 1024 * 1024  # 20MB default cap; adjust per your infra

class FileSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    class Meta:
        model = File
        fields = [
            "id","original_name","mime_type","size_bytes","sha256",
            "facility","patient","visibility","tag","created_at","url"
        ]
        read_only_fields = ["size_bytes","sha256","created_at"]

    def get_url(self, obj):
        # Direct MEDIA URL; consider signed URLs if using S3 later
        try:
            return obj.file.url
        except Exception:
            return None

class UploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    patient = serializers.IntegerField(required=False)  # Patient.id
    visibility = serializers.ChoiceField(choices=Visibility.choices, default=Visibility.PRIVATE)
    tag = serializers.CharField(required=False, allow_blank=True)

    def validate_file(self, f):
        if f.size > MAX_SIZE_BYTES:
            raise serializers.ValidationError("File too large (>20MB).")
        return f

class LinkSerializer(serializers.Serializer):
    file_id = serializers.IntegerField()
    app_label = serializers.CharField()   # e.g., "imaging"
    model = serializers.CharField()       # e.g., "imagingreport"
    object_id = serializers.IntegerField()

    def save(self):
        file_id = self.validated_data["file_id"]
        ct = ContentType.objects.get(app_label=self.validated_data["app_label"],
                                     model=self.validated_data["model"])
        return AttachmentLink.objects.get_or_create(
            file_id=file_id, content_type=ct, object_id=self.validated_data["object_id"]
        )[0]
