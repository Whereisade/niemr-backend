import hashlib
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import FileExtensionValidator
from django.db import models
from facilities.models import Facility
from patients.models import Patient
from .enums import Visibility

ALLOWED_EXTS = ["pdf","jpg","jpeg","png","tif","tiff","bmp","gif"]

def upload_path(instance, filename):
    # media/attachments/{facility|global}/{patient|none}/{hash[:8]}/{filename}
    scope = f"f{instance.facility_id}" if instance.facility_id else "global"
    pscope = f"p{instance.patient_id}" if instance.patient_id else "none"
    return f"attachments/{scope}/{pscope}/{filename}"

class File(models.Model):
    """
    Single source of truth for uploaded files.
    Link to any object via AttachmentLink or store the FK on the feature model.
    """
    file = models.FileField(upload_to=upload_path,
                            validators=[FileExtensionValidator(allowed_extensions=ALLOWED_EXTS)])
    original_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)

    # ownership / scoping
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    facility = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.SET_NULL, related_name="files")
    patient = models.ForeignKey(Patient, null=True, blank=True, on_delete=models.SET_NULL, related_name="files")
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.PRIVATE)

    # optional tags for organization (e.g., "Lab", "CT", "Referral")
    tag = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["facility","patient","created_at"]),
            models.Index(fields=["visibility"]),
            models.Index(fields=["sha256"]),
        ]
        ordering = ["-created_at","-id"]

    def save(self, *args, **kwargs):
        # capture size & sha256 on first save or when file replaced
        if self.file and (not self.size_bytes or not self.sha256):
            self.size_bytes = self.file.size or 0
            # read small chunks to hash
            h = hashlib.sha256()
            for chunk in self.file.chunks():
                h.update(chunk)
            self.sha256 = h.hexdigest()
        # infer facility from patient if missing
        if not self.facility_id and self.patient_id and self.patient.facility_id:
            self.facility_id = self.patient.facility_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"File#{self.id} {self.original_name}"

class AttachmentLink(models.Model):
    """
    Generic relation to attach a File to any object (e.g., Encounter, ImagingReport, LabOrder).
    Keeps API independent of those apps and avoids circular imports.
    """
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="links")
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("file","content_type","object_id")
