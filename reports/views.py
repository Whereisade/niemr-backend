from django.conf import settings
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .serializers import GenerateRequestSerializer
from .models import ReportJob, ReportType
from .services.pdf import render_html, try_render_pdf
from .services.context import encounter_context, lab_context, imaging_context, billing_context
from .services.save_as_attachment import save_pdf_as_attachment

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def generate(request):
    """
    POST /api/reports/generate
    {
      "report_type":"IMAGING",
      "ref_id": 55,
      "as_pdf": true,
      "save_as_attachment": true,
      "start":"2025-10-01T00:00:00Z", "end":"2025-10-31T23:59:59Z"  # (billing only)
    }
    """
    s = GenerateRequestSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    data = s.validated_data
    rtype = data["report_type"]
    ref_id = data["ref_id"]
    as_pdf = data["as_pdf"]

    # build context + template
    if rtype == ReportType.ENCOUNTER:
        ctx = encounter_context(ref_id)
        tpl = "reports/encounter.html"
        filename = f"encounter-{ref_id}.pdf"
        patient = ctx["patient"]; facility = ctx["facility"]
    elif rtype == ReportType.LAB:
        ctx = lab_context(ref_id)
        tpl = "reports/lab.html"
        filename = f"lab-{ref_id}.pdf"
        patient = ctx["patient"]; facility = ctx["facility"]
    elif rtype == ReportType.IMAGING:
        ctx = imaging_context(ref_id)
        tpl = "reports/imaging.html"
        filename = f"imaging-{ref_id}.pdf"
        patient = ctx["patient"]; facility = ctx["facility"]
    elif rtype == ReportType.BILLING:
        ctx = billing_context(ref_id, start=data.get("start"), end=data.get("end"))
        tpl = "reports/billing.html"
        filename = f"billing-{ref_id}.pdf"
        patient = ctx["patient"]; facility = ctx["facility"]
    else:
        return Response({"detail":"Unknown report_type"}, status=400)

    ctx["title"] = filename.replace(".pdf","").replace("-"," ").title()
    html = render_html(tpl, ctx)

    # try PDF
    resp = None
    if as_pdf:
        resp = try_render_pdf(html, filename=filename)

    # Persist a job record
    job = ReportJob.objects.create(
        report_type=rtype, ref_id=ref_id, created_by=request.user, format="PDF" if as_pdf else "HTML"
    )

    # save into attachments if requested and we have a PDF
    if data.get("save_as_attachment") and resp and resp.content:
        file_obj = save_pdf_as_attachment(
            filename=filename, pdf_bytes=resp.content, user=request.user, patient=patient, facility=facility, tag="Report"
        )
        job.saved_as_attachment_id = file_obj.id
        job.save(update_fields=["saved_as_attachment_id"])

    # Return response
    if resp:  # PDF
        return resp
    # HTML fallback
    return HttpResponse(html, content_type="text/html")
