from attachments.models import File
from django.core.files.base import ContentFile

def save_pdf_as_attachment(*, filename: str, pdf_bytes: bytes, user, patient=None, facility=None, tag="Report"):
    return File.objects.create(
        file=ContentFile(pdf_bytes, name=filename),
        original_name=filename,
        mime_type="application/pdf",
        uploaded_by=user,
        patient=patient,
        facility=facility,
        visibility="PATIENT" if patient else "PRIVATE",
        tag=tag,
    )
