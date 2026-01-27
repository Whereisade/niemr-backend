"""reports/utils.py

Backs POST /api/reports/generate/.

Patch v2:
- Never return HTML bytes as a PDF (corrupt "damaged" downloads).
- If PDF generation fails, raise a clear API error with the underlying WeasyPrint exception.
- Uses reports/services/context.py for template contexts.
- BILLING is anchored on Patient, with optional Charge-id receipt generation.
"""

from __future__ import annotations

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.template.loader import render_to_string

from rest_framework.exceptions import APIException, ValidationError


class PdfGenerationError(APIException):
    status_code = 500
    default_code = "pdf_generation_failed"
    default_detail = "PDF generation failed. Please check server PDF dependencies."


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
        # In this codebase, billing reports are patient statements.
        "model": ("patients", "Patient"),
        "template": "reports/billing.html",
        "tag": "REPORT_BILLING",
    },
    "HMO_STATEMENT": {
        # Facility/provider scoped HMO relationship statement.
        "model": ("patients", "FacilityHMO"),
        "template": "reports/hmo_statement.html",
        "tag": "REPORT_HMO_STATEMENT",
    },
}


def get_report_object(report_type: str, ref_id: int):
    """Resolve the object that anchors the report.

    For ENCOUNTER/LAB/IMAGING -> object is the PK instance.

    For BILLING -> object is a Patient.
      - If ref_id matches a patient id, we use that.
      - Else, if ref_id matches a Charge id, we generate a "receipt-like"
        statement limited to that charge.

    Returns (obj, cfg) where cfg may include internal metadata.
    """
    base_cfg = REPORT_CONFIG.get(report_type)
    if not base_cfg:
        raise ValidationError({"report_type": "Unsupported report type"})

    cfg = dict(base_cfg)

    app_label, model_name = cfg["model"]
    model = apps.get_model(app_label, model_name)

    try:
        obj = model.objects.get(pk=ref_id)
        return obj, cfg
    except model.DoesNotExist:
        pass

    if report_type == "BILLING":
        Charge = apps.get_model("billing", "Charge")
        try:
            charge = Charge.objects.select_related("patient").get(pk=ref_id)
        except Charge.DoesNotExist:
            raise ValidationError({"ref_id": "Object not found for this report type"})
        cfg["_charge_id"] = charge.pk
        return charge.patient, cfg

    raise ValidationError({"ref_id": "Object not found for this report type"})


def render_report_html(report_type: str, obj, cfg: dict, *, start=None, end=None) -> str:
    """Render report HTML using the context builders in reports/services/context.py."""
    template_name = cfg["template"]

    from reports.services.context import (
        billing_context,
        encounter_context,
        hmo_statement_context,
        imaging_context,
        lab_context,
    )

    if report_type == "ENCOUNTER":
        context = encounter_context(obj.id)
    elif report_type == "LAB":
        context = lab_context(obj.id)
    elif report_type == "IMAGING":
        context = imaging_context(obj.id)
    elif report_type == "BILLING":
        charge_id = cfg.get("_charge_id")
        context = billing_context(obj.id, start=start, end=end, charge_id=charge_id)
    elif report_type == "HMO_STATEMENT":
        context = hmo_statement_context(obj.id, start=start, end=end)
    else:
        raise ValidationError({"report_type": "Unsupported report type"})

    context.setdefault("title", f"{report_type.title()} Report")
    return render_to_string(template_name, context)


def build_pdf(html: str) -> bytes:
    """Convert HTML to PDF bytes using WeasyPrint.

    Important: never return HTML bytes here. If WeasyPrint isn't working,
    raise a server error so the client doesn't download a corrupt PDF.
    """
    base_url = getattr(settings, "STATIC_ROOT", None) or getattr(settings, "BASE_DIR", ".")

    try:
        from weasyprint import HTML  # type: ignore

        pdf_bytes = HTML(string=html, base_url=str(base_url)).write_pdf()
    except Exception as exc:  # noqa: BLE001
        raise PdfGenerationError(
            detail=(
                "PDF generation failed (WeasyPrint). "
                "This usually means OS-level rendering dependencies are missing. "
                f"Underlying error: {exc}"
            )
        ) from exc

    # sanity check
    if not isinstance(pdf_bytes, (bytes, bytearray)) or not bytes(pdf_bytes).startswith(b"%PDF-"):
        raise PdfGenerationError(
            detail="PDF generation returned invalid bytes (did not start with %PDF-)."
        )

    return bytes(pdf_bytes)


def save_report_attachment(obj, pdf_bytes: bytes, filename: str, tag: str, user=None):
    """Save the PDF as an attachment linked to the object."""
    from attachments.enums import Visibility
    from attachments.models import AttachmentLink, File

    patient = getattr(obj, "patient", None)

    f = File.objects.create(
        file=ContentFile(pdf_bytes, name=filename),
        original_name=filename,
        uploaded_by=user,
        patient=patient,
        tag=tag,
        visibility=Visibility.PRIVATE,
        mime_type="application/pdf",
    )

    ct = ContentType.objects.get_for_model(obj.__class__)
    AttachmentLink.objects.create(
        file=f,
        content_type=ct,
        object_id=obj.pk,
    )

    return f
