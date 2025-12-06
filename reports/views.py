# reports/views.py
from django.utils import timezone
from django.http import HttpResponse

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .serializers import GenerateReportSerializer
from .utils import (
    get_report_object,
    render_report_html,
    build_pdf,
    save_report_attachment,
)


class GenerateReportView(APIView):
    """
    POST /api/reports/generate/

    Body:
    {
      "report_type": "ENCOUNTER" | "LAB" | "IMAGING" | "BILLING",
      "ref_id": 42,
      "as_pdf": true,
      "save_as_attachment": true
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = GenerateReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        report_type = data["report_type"]
        ref_id = data["ref_id"]
        as_pdf = data["as_pdf"]
        save_as_attachment_flag = data["save_as_attachment"]

        obj, cfg = get_report_object(report_type, ref_id)
        html = render_report_html(report_type, obj, cfg)

        # Build filename like encounter-42-20251205-120000.pdf
        timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        base_name = f"{report_type.lower()}-{ref_id}-{timestamp}"

        if as_pdf:
            pdf_bytes = build_pdf(html)
            filename = f"{base_name}.pdf"

            # Optional: save as attachment
            attachment = None
            if save_as_attachment_flag:
                attachment = save_report_attachment(
                    obj=obj,
                    pdf_bytes=pdf_bytes,
                    filename=filename,
                    tag=cfg.get("tag", "REPORT"),
                    user=request.user,
                )

            # Respond with the PDF directly
            resp = HttpResponse(pdf_bytes, content_type="application/pdf")
            resp["Content-Disposition"] = f'attachment; filename="{filename}"'

            # You could also include some headers with attachment id if you like
            if attachment is not None:
                resp["X-Attachment-Id"] = str(attachment.pk)

            return resp

        # as_pdf = False → just return HTML (and optionally save nothing)
        filename = f"{base_name}.html"

        if save_as_attachment_flag:
            # If you really want HTML saved too, you can adapt save_report_attachment
            # to accept html_bytes instead. For now we just skip saving here.
            pass

        return Response(
            {
                "report_type": report_type,
                "ref_id": ref_id,
                "html": html,
                "filename": filename,
            },
            status=status.HTTP_200_OK,
        )
