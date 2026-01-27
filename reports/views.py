# reports/views.py

from django.http import HttpResponse
from django.utils import timezone

from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from facilities.permissions_utils import has_facility_permission

from .serializers import GenerateReportSerializer
from .utils import build_pdf, get_report_object, render_report_html, save_report_attachment


def _ensure_scoped_access(user, report_type: str, obj):
    """Prevent cross-facility/provider report access.

    This codebase is multi-tenant:
    - Facility users should only access objects linked to their facility
    - Independent providers should only access objects they own (where applicable)
    """
    facility_id = getattr(user, "facility_id", None)

    # Facility-scoped users
    if facility_id:
        # Most report anchor models have a facility_id field
        obj_facility_id = getattr(obj, "facility_id", None)
        if obj_facility_id and obj_facility_id != facility_id:
            raise NotFound("Not found.")

        # Patient billing statement
        if report_type == "BILLING":
            if getattr(obj, "facility_id", None) and obj.facility_id != facility_id:
                raise NotFound("Not found.")

        # FacilityHMO statement
        if report_type == "HMO_STATEMENT":
            if getattr(obj, "facility_id", None) and obj.facility_id != facility_id:
                raise NotFound("Not found.")

        return

    # Independent provider users
    # (Some objects won't have owner_id; we fail open to stay backward compatible)
    if report_type == "HMO_STATEMENT":
        obj_owner_id = getattr(obj, "owner_id", None)
        if obj_owner_id and obj_owner_id != getattr(user, "id", None):
            raise NotFound("Not found.")


def _ensure_permission(user, report_type: str, obj):
    """Role-based access control for reports (facility role permissions)."""
    if report_type == "BILLING":
        if not (
            has_facility_permission(user, "can_view_billing")
            or has_facility_permission(user, "can_manage_payments")
        ):
            raise PermissionDenied("You do not have permission to view billing statements.")

    if report_type == "HMO_STATEMENT":
        if not (
            has_facility_permission(user, "can_view_billing")
            or has_facility_permission(user, "can_manage_payments")
            or has_facility_permission(user, "can_manage_hmo_pricing")
        ):
            raise PermissionDenied("You do not have permission to view HMO statements.")

    if report_type == "ENCOUNTER":
        # If role is restricted, only allow if the user is involved.
        if not has_facility_permission(user, "can_view_all_encounters"):
            involved = False
            for field in ("created_by_id", "provider_id", "nurse_id"):
                if getattr(obj, field, None) == getattr(user, "id", None):
                    involved = True
                    break
            if not involved:
                raise PermissionDenied("You do not have permission to view this encounter report.")

    if report_type == "LAB":
        if not (
            has_facility_permission(user, "can_view_lab_orders")
            or has_facility_permission(user, "can_process_lab_orders")
        ):
            if getattr(obj, "ordered_by_id", None) != getattr(user, "id", None):
                raise PermissionDenied("You do not have permission to view this lab report.")

    # IMAGING: no explicit permission field in FacilityRolePermission yet.


class GenerateReportView(APIView):
    """POST /api/reports/generate/

    Body:
    {
      "report_type": "ENCOUNTER" | "LAB" | "IMAGING" | "BILLING" | "HMO_STATEMENT",
      "ref_id": 42,
      "as_pdf": true,
      "save_as_attachment": false,
      "start": "YYYY-MM-DD" (optional),
      "end": "YYYY-MM-DD" (optional)
    }
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = GenerateReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        report_type = data["report_type"]
        ref_id = data["ref_id"]
        as_pdf = data["as_pdf"]
        save_as_attachment_flag = data["save_as_attachment"]
        start = data.get("start")
        end = data.get("end")

        obj, cfg = get_report_object(report_type, ref_id)

        # Enforce tenant boundaries + role-based permissions
        _ensure_scoped_access(request.user, report_type, obj)
        _ensure_permission(request.user, report_type, obj)

        html = render_report_html(report_type, obj, cfg, start=start, end=end)

        # Build filename like encounter-42-20251205-120000.pdf
        timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        base_name = f"{report_type.lower()}-{ref_id}-{timestamp}"

        if as_pdf:
            pdf_bytes = build_pdf(html)
            filename = f"{base_name}.pdf"

            attachment = None
            if save_as_attachment_flag:
                attachment = save_report_attachment(
                    obj=obj,
                    pdf_bytes=pdf_bytes,
                    filename=filename,
                    tag=cfg.get("tag", "REPORT"),
                    user=request.user,
                )

            resp = HttpResponse(pdf_bytes, content_type="application/pdf")
            resp["Content-Disposition"] = f'attachment; filename="{filename}"'
            if attachment is not None:
                resp["X-Attachment-Id"] = str(attachment.pk)
            return resp

        filename = f"{base_name}.html"
        return Response(
            {
                "report_type": report_type,
                "ref_id": ref_id,
                "html": html,
                "filename": filename,
            },
            status=status.HTTP_200_OK,
        )
