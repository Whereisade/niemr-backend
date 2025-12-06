# reports/utils.py
from django.apps import apps
from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.contrib.contenttypes.models import ContentType

from rest_framework.exceptions import ValidationError

# Try to import a PDF engine (WeasyPrint).
# If you don't want this yet, you can temporarily make build_pdf just return html_bytes.
try:
    from weasyprint import HTML  # type: ignore
except Exception:  # noqa: BLE001
    HTML = None


# 🔁 Adjust these model names to your actual ones if different
REPORT_CONFIG = {
    "ENCOUNTER": {
        "model": ("encounters", "Encounter"),
        "template": "reports/encounter.html",
        "tag": "REPORT_ENCOUNTER",
    },
    "LAB": {
        "model": ("labs", "LabOrder"),
        "template": "reports/lab.html",
        "tag": "REPORT_LAB",
    },
    "IMAGING": {
        "model": ("imaging", "ImagingRequest"),
        "template": "reports/imaging.html",
        "tag": "REPORT_IMAGING",
    },
    "BILLING": {
        "model": ("billing", "Invoice"),  # or BillingRecord / Payment etc.
        "template": "reports/billing.html",
        "tag": "REPORT_BILLING",
    },
}


def get_report_object(report_type: str, ref_id: int):
    cfg = REPORT_CONFIG.get(report_type)
    if not cfg:
        raise ValidationError({"report_type": "Unsupported report type"})

    app_label, model_name = cfg["model"]
    model = apps.get_model(app_label, model_name)

    try:
        obj = model.objects.get(pk=ref_id)
    except model.DoesNotExist:
        raise ValidationError({"ref_id": "Object not found for this report type"})

    return obj, cfg


def render_report_html(report_type: str, obj, cfg: dict) -> str:
    """
    Minimal HTML rendering. You can make templates as fancy as you like later.
    """
    template_name = cfg["template"]

    context = {
        "report_type": report_type,
        "object": obj,
        "config": cfg,
    }

    # You will need to create these templates in templates/reports/*.html
    return render_to_string(template_name, context)


def build_pdf(html: str) -> bytes:
    """
    Turn HTML into a PDF.

    DEV MODE (no WeasyPrint installed):
    - If WeasyPrint isn't available, just return the HTML bytes.
      The view will still send it as application/pdf so your flow
      and attachments logic can be wired and tested.
    """
    if HTML is None:
        # ⚠️ Stub: not a real PDF, just HTML bytes.
        return html.encode("utf-8")

    base_url = getattr(settings, "STATIC_ROOT", None) or getattr(settings, "BASE_DIR", ".")
    return HTML(string=html, base_url=base_url).write_pdf()


def save_report_attachment(obj, pdf_bytes: bytes, filename: str, tag: str, user=None):
    """
    Save the PDF as an attachment linked to the object.

    ⚠️ Adjust to match your attachments models/field names.
    Assumes something like:

        from attachments.models import File, AttachmentLink

        class File(models.Model):
            file = FileField(...)
            original_name = CharField(...)
            uploaded_by = FK(User)
            patient = FK(Patient, null=True)
            tag = CharField(...)
            visibility = CharField(...)

        class AttachmentLink(models.Model):
            file = FK(File)
            content_type = FK(ContentType)
            object_id = PositiveIntegerField()
            content_object = GenericForeignKey(...)
    """
    from attachments.models import File, AttachmentLink  # adjust if different

    # infer patient if the object has a .patient attribute
    patient = getattr(obj, "patient", None)

    f = File.objects.create(
        file=ContentFile(pdf_bytes, name=filename),
        original_name=filename,
        uploaded_by=user,
        patient=patient,
        tag=tag,
        visibility="STAFF",  # or whatever choices you use
    )

    ct = ContentType.objects.get_for_model(obj.__class__)
    AttachmentLink.objects.create(
        file=f,
        content_type=ct,
        object_id=obj.pk,
    )

    return f
