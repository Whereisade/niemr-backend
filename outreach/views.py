from __future__ import annotations

import csv
import io
import math
from datetime import datetime

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q, Count
from django.http import FileResponse, Http404
from django.utils import timezone

from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import PermissionDenied

from .constants import (
    MODULE_VITALS, MODULE_ENCOUNTER, MODULE_LAB, MODULE_PHARMACY, MODULE_IMMUNIZATION,
    MODULE_BLOOD_DONATION, MODULE_COUNSELING, MODULE_MATERNAL,
    MODULE_REFERRAL, MODULE_SURGICALS, MODULE_EYE_CHECKS, MODULE_DENTAL_CHECKS,
    PERM_PATIENTS_VIEW, PERM_PATIENTS_CREATE, PERM_PATIENTS_EDIT,
    PERM_VITALS_CREATE, PERM_VITALS_EDIT,
    PERM_ENCOUNTER_CREATE, PERM_ENCOUNTER_EDIT,
    PERM_LAB_CATALOG_VIEW, PERM_LAB_CATALOG_MANAGE,
    PERM_LAB_ORDER_CREATE, PERM_LAB_ORDER_EDIT,
    PERM_LAB_RESULT_CREATE, PERM_LAB_RESULT_EDIT,
    PERM_PHARMACY_CATALOG_VIEW, PERM_PHARMACY_CATALOG_MANAGE,
    PERM_PHARMACY_DISPENSE_CREATE, PERM_PHARMACY_DISPENSE_EDIT,
    PERM_IMMUNIZATION_CREATE, PERM_IMMUNIZATION_EDIT,
    PERM_BLOOD_CREATE, PERM_BLOOD_EDIT,
    PERM_COUNSELING_CREATE, PERM_COUNSELING_EDIT, PERM_COUNSELING_VIEW_SENSITIVE,
    PERM_MATERNAL_CREATE, PERM_MATERNAL_EDIT,
    PERM_REFERRAL_CREATE, PERM_REFERRAL_EDIT,
    PERM_SURGICALS_CREATE, PERM_SURGICALS_EDIT,
    PERM_EYE_CHECKS_CREATE, PERM_EYE_CHECKS_EDIT,
    PERM_DENTAL_CHECKS_CREATE, PERM_DENTAL_CHECKS_EDIT,
    PERM_REPORTS_VIEW, PERM_REPORTS_EXPORT,
)
from .enums import OutreachStatus, LabOrderStatus, CounselingVisibility
from .importers import read_tabular_file
from .models import (
    OutreachEvent, OutreachSite, OutreachStaffProfile,
    OutreachPatient, OutreachVitals, OutreachEncounter,
    OutreachLabTestCatalog, OutreachLabOrder, OutreachLabOrderItem, OutreachLabResult,
    OutreachDrugCatalog, OutreachDispense, OutreachVaccineCatalog,
    OutreachImmunization, OutreachBloodDonation,
    OutreachReferral, OutreachSurgical, OutreachEyeCheck, OutreachDentalCheck,
    OutreachCounseling, OutreachMaternal,
    OutreachAuditLog, OutreachExport,
)
from .permissions import (
    IsOutreachSuperAdmin, IsOutreachStaff,
    is_outreach_super_admin, get_active_profiles, get_profile_for_event,
    has_outreach_permission, ensure_outreach_writeable, ensure_module_enabled,
)
from .serializers import (
    OutreachEventSerializer, OutreachEventDetailSerializer,
    OutreachSiteSerializer,
    OutreachStaffProfileSerializer, OutreachColleagueSerializer, OutreachStaffCreateSerializer, OutreachStaffUpdateSerializer,
    OutreachPatientSerializer,
    OutreachVitalsSerializer,
    OutreachEncounterSerializer,
    OutreachLabTestSerializer, OutreachLabOrderSerializer, OutreachLabOrderCreateSerializer, OutreachLabResultSerializer,
    OutreachDrugSerializer, OutreachDispenseSerializer, OutreachDispenseCreateSerializer,
    OutreachVaccineCatalogSerializer,
    OutreachImmunizationSerializer, OutreachBloodDonationSerializer,
    OutreachReferralSerializer, OutreachSurgicalSerializer, OutreachEyeCheckSerializer, OutreachDentalCheckSerializer,
    OutreachCounselingSerializer, OutreachMaternalSerializer,
    OutreachAuditLogSerializer,
    OutreachExportSerializer,
)
from .utils import allocate_next_patient_code, create_or_reset_outreach_user, log_action


# ------------------------
# Helpers
# ------------------------

def _pick_event_for_request(request, *, allow_closed: bool = True) -> OutreachEvent:
    """Resolve outreach event for request.

    Priority:
    - event_id from query params or request.data
    - if staff has exactly 1 active profile -> that event
    - if super admin and no event_id -> error (needs explicit)
    """
    event_id = request.query_params.get("event_id") or request.data.get("event_id")
    if event_id:
        event = OutreachEvent.objects.filter(id=event_id).first()
        if not event:
            raise Http404("Outreach event not found.")
        if not allow_closed and event.status == OutreachStatus.CLOSED:
            raise Http404("Outreach event is closed.")

        u = request.user
        # Prevent cross-tenant access when event_id is supplied explicitly.
        if is_outreach_super_admin(u):
            # Platform admins can see all events; normal outreach super admins can only see their own.
            if (not _is_platform_admin(u)) and event.created_by_id != getattr(u, 'id', None):
                raise Http404("Outreach event not found.")
        else:
            # Staff must be actively assigned to this outreach.
            if not get_profile_for_event(u, event):
                raise Http404("Outreach event not found.")

        return event

    u = request.user
    if is_outreach_super_admin(u):
        raise ValueError("event_id is required for this action.")
    profiles = get_active_profiles(u)
    if profiles.count() != 1:
        raise ValueError("event_id is required (you have multiple active outreach assignments).")
    return profiles.first().outreach_event


def _require_perm(request, event: OutreachEvent, perm_key: str):
    if not has_outreach_permission(request.user, event, perm_key):
        return Response({"detail": "You do not have permission to perform this action."}, status=403)
    return None


def _staff_sites_context(user, event: OutreachEvent):
    if is_outreach_super_admin(user):
        return {"all_sites": True, "site_ids": []}
    profile = get_profile_for_event(user, event)
    if not profile:
        return {"all_sites": False, "site_ids": []}
    if profile.all_sites:
        return {"all_sites": True, "site_ids": []}
    return {"all_sites": False, "site_ids": list(profile.sites.values_list("id", flat=True))}


def _validate_patient_site(request, event: OutreachEvent, site: OutreachSite | None):
    ctx = _staff_sites_context(request.user, event)
    if ctx["all_sites"]:
        return True
    if not site:
        return False
    return site.id in ctx["site_ids"]


def _csv_bytes(headers, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8")


def _is_platform_admin(user) -> bool:
    """Platform/system admin (Django staff/superuser) can see all outreach events."""
    return bool(getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False))


def _scope_super_admin_qs(qs, user):
    """For Outreach Super Admin accounts (non-platform admins), scope queries to events they created.

    This prevents one outreach super admin from seeing another super admin's outreach data.
    """
    if _is_platform_admin(user):
        return qs
    return qs.filter(outreach_event__created_by=user)


# ------------------------
# Staff Portal
# ------------------------

@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsOutreachStaff])
def my_event(request):
    """Return active outreach assignment(s) for the logged-in staff."""
    u = request.user
    if is_outreach_super_admin(u):
        return Response({"detail": "Super admin does not have a single assigned outreach. Use /events/."}, status=200)

    profiles = (
        get_active_profiles(u)
        .select_related("outreach_event")
        .prefetch_related("sites")
    )

    data = []
    for p in profiles:
        evt = p.outreach_event

        # Only expose sites the staff can actually use.
        if p.all_sites:
            accessible_sites = OutreachSite.objects.filter(outreach_event=evt).order_by("name")
        else:
            accessible_sites = p.sites.all().order_by("name")

        evt_data = OutreachEventDetailSerializer(evt, context={"request": request}).data
        evt_data["sites"] = OutreachSiteSerializer(accessible_sites, many=True).data

        # Align sites count with what the staff can see/use.
        stats = evt_data.get("stats") or {}
        try:
            stats = dict(stats)
        except Exception:
            stats = {}
        stats["sites"] = accessible_sites.count()
        evt_data["stats"] = stats

        data.append({
            "profile_id": p.id,
            "event": evt_data,
            "permissions": p.permissions,
            "all_sites": p.all_sites,
            "sites": evt_data["sites"],
        })

    return Response({"assignments": data})



@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsOutreachStaff])
def colleagues(request):
    """List colleagues (staff profiles) within the selected outreach event for staff users.

    Super Admin should use the /events/{id}/staff endpoint instead.
    """
    u = request.user
    if is_outreach_super_admin(u):
        return Response({"detail": "Super admin should use /events/{id}/staff/."}, status=400)

    try:
        evt = _pick_event_for_request(request, allow_closed=True)
    except Exception as e:
        return Response({"detail": str(e)}, status=400)

    qs = (
        OutreachStaffProfile.objects
        .filter(outreach_event=evt, is_active=True)
        .select_related("user")
        .prefetch_related("sites")
        .order_by("user__first_name", "user__last_name", "user__email")
    )
    return Response(OutreachColleagueSerializer(qs, many=True, context={"request": request}).data)

# ------------------------
# Outreach Super Admin - Events
# ------------------------

class OutreachEventViewSet(viewsets.ModelViewSet):
    queryset = OutreachEvent.objects.all().order_by("-created_at")
    serializer_class = OutreachEventSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachSuperAdmin]

    def get_queryset(self):
        qs = super().get_queryset()
        u = getattr(self.request, 'user', None)
        if not u:
            return qs.none()
        if _is_platform_admin(u):
            return qs
        return qs.filter(created_by=u)


    def get_serializer_class(self):
        if self.action in ("retrieve",):
            return OutreachEventDetailSerializer
        return OutreachEventSerializer

    def perform_create(self, serializer):
        evt = serializer.save(created_by=self.request.user)
        log_action(evt, self.request.user, "outreach.event.created", {"title": evt.title})

    def perform_update(self, serializer):
        evt = self.get_object()
        if evt.status == OutreachStatus.CLOSED:
            raise PermissionDenied("Closed outreach is read-only.")
        obj = serializer.save()
        log_action(obj, self.request.user, "outreach.event.updated", {"id": obj.id})

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        evt = self.get_object()
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Outreach is already closed."}, status=400)
        evt.status = OutreachStatus.ACTIVE
        evt.save(update_fields=["status"])
        log_action(evt, request.user, "outreach.event.activated")
        return Response(OutreachEventDetailSerializer(evt).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        evt = self.get_object()
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Outreach is already closed."}, status=400)

        evt.status = OutreachStatus.CLOSED
        evt.closed_at = timezone.now()
        evt.save(update_fields=["status", "closed_at"])

        # Disable staff profiles + users
        profiles = OutreachStaffProfile.objects.select_related("user").filter(outreach_event=evt, is_active=True)
        disabled = 0
        for p in profiles:
            p.is_active = False
            p.disabled_at = timezone.now()
            p.save(update_fields=["is_active", "disabled_at"])
            try:
                user = p.user
                user.is_active = False
                user.set_unusable_password()
                user.save(update_fields=["is_active", "password"])
            except Exception:
                pass
            disabled += 1

        log_action(evt, request.user, "outreach.event.closed", {"disabled_staff": disabled})
        return Response({"detail": "Outreach closed. All staff access disabled.", "disabled_staff": disabled})

    # ---- Sites management (OSA)
    @action(detail=True, methods=["get", "post"], url_path="sites")
    def sites(self, request, pk=None):
        evt = self.get_object()
        if request.method == "GET":
            qs = OutreachSite.objects.filter(outreach_event=evt).order_by("name")
            return Response(OutreachSiteSerializer(qs, many=True).data)

        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)

        s = OutreachSiteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        site = s.save(outreach_event=evt)
        log_action(evt, request.user, "outreach.site.created", {"site_id": site.id, "name": site.name})
        return Response(OutreachSiteSerializer(site).data, status=201)

    # ---- Staff management (OSA)
    @action(detail=True, methods=["get", "post"], url_path="staff")
    def staff(self, request, pk=None):
        evt = self.get_object()

        if request.method == "GET":
            qs = OutreachStaffProfile.objects.filter(outreach_event=evt).select_related("user").prefetch_related("sites").order_by("-created_at")
            return Response(OutreachStaffProfileSerializer(qs, many=True).data)

        # POST create staff
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)

        s = OutreachStaffCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        email = s.validated_data["email"]
        phone = (s.validated_data.get("phone") or "").strip()
        full_name = s.validated_data.get("full_name", "")
        role_template = s.validated_data.get("role_template", "")
        permissions = s.validated_data.get("permissions", [])
        all_sites = s.validated_data.get("all_sites", True)
        site_ids = s.validated_data.get("site_ids", [])

        account_role = s.get_account_role()

        user, created, raw_password = create_or_reset_outreach_user(email=email, full_name=full_name, role=account_role)

        profile, _ = OutreachStaffProfile.objects.get_or_create(
            outreach_event=evt,
            user=user,
            defaults={
                "phone": phone,
                "role_template": role_template,
                "permissions": permissions,
                "all_sites": bool(all_sites),
                "created_by": request.user,
            }
        )
        if not _:
            # update profile if exists
            profile.phone = phone
            profile.role_template = role_template
            profile.permissions = permissions
            profile.all_sites = bool(all_sites)
            profile.is_active = True
            profile.disabled_at = None
            profile.created_by = request.user
            profile.save(update_fields=["phone","role_template","permissions","all_sites","is_active","disabled_at","created_by"])

        if not profile.all_sites:
            sites = OutreachSite.objects.filter(outreach_event=evt, id__in=site_ids)
            profile.sites.set(sites)
        else:
            profile.sites.clear()

        log_action(evt, request.user, "outreach.staff.created", {"profile_id": profile.id, "email": email, "role_template": role_template})

        # Return password ONCE (frontend should display and optionally allow download)
        data = OutreachStaffProfileSerializer(profile).data
        data["credentials"] = {"email": email, "password": raw_password}
        return Response(data, status=201)

    @action(detail=True, methods=["patch"], url_path=r"staff/(?P<profile_id>[^/.]+)")
    def staff_update(self, request, pk=None, profile_id=None):
        """Update a staff profile (role_template, permissions, all_sites, site_ids, is_active)."""
        evt = self.get_object()

        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)

        profile = (
            OutreachStaffProfile.objects
            .filter(outreach_event=evt, id=profile_id)
            .select_related("user")
            .prefetch_related("sites")
            .first()
        )
        if not profile:
            return Response({"detail": "Staff profile not found."}, status=404)

        s = OutreachStaffUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        vd = s.validated_data

        if "phone" in vd:
            profile.phone = (vd.get("phone") or "").strip()

        if "role_template" in vd:
            profile.role_template = (vd.get("role_template") or "").strip()
        if "permissions" in vd:
            profile.permissions = vd.get("permissions", [])
        if "all_sites" in vd:
            profile.all_sites = bool(vd["all_sites"])

        if "is_active" in vd:
            is_active = bool(vd["is_active"])
            profile.is_active = is_active
            profile.disabled_at = None if is_active else timezone.now()
            try:
                profile.user.is_active = is_active
                profile.user.save(update_fields=["is_active"])
            except Exception:
                pass

        profile.save()

        site_ids = vd.get("site_ids", None)
        if profile.all_sites:
            profile.sites.clear()
        elif site_ids is not None:
            qs = OutreachSite.objects.filter(outreach_event=evt, id__in=site_ids)
            found = set(qs.values_list("id", flat=True))
            missing = [sid for sid in site_ids if sid not in found]
            if missing:
                return Response({"site_ids": [f"Invalid site ids: {missing}"]}, status=400)
            profile.sites.set(list(qs))

        profile = (
            OutreachStaffProfile.objects
            .filter(id=profile.id)
            .select_related("user")
            .prefetch_related("sites")
            .first()
        )
        log_action(evt, request.user, "outreach.staff.updated", {"profile_id": profile.id})
        return Response(OutreachStaffProfileSerializer(profile).data)


    @action(detail=True, methods=["post"], url_path=r"staff/(?P<profile_id>[^/.]+)/reset-password")
    def staff_reset_password(self, request, pk=None, profile_id=None):
        evt = self.get_object()
        if evt.status != OutreachStatus.ACTIVE:
            return Response({"detail": "Password reset is only allowed while outreach is ACTIVE."}, status=400)
        profile = OutreachStaffProfile.objects.select_related("user").filter(outreach_event=evt, id=profile_id).first()
        if not profile:
            return Response({"detail": "Staff profile not found."}, status=404)

        user = profile.user
        # Reset
        user.set_password(None)  # set unusable first then set real below via helper
        user.save(update_fields=["password"])
        user, _, raw_password = create_or_reset_outreach_user(email=user.email, full_name=f"{user.first_name} {user.last_name}".strip(), role=user.role)

        profile.is_active = True
        profile.disabled_at = None
        profile.save(update_fields=["is_active","disabled_at"])

        log_action(evt, request.user, "outreach.staff.reset_password", {"profile_id": profile.id, "email": user.email})
        return Response({"detail": "Password reset.", "credentials": {"email": user.email, "password": raw_password}})

    @action(detail=True, methods=["post"], url_path=r"staff/(?P<profile_id>[^/.]+)/disable")
    def staff_disable(self, request, pk=None, profile_id=None):
        evt = self.get_object()
        profile = OutreachStaffProfile.objects.select_related("user").filter(outreach_event=evt, id=profile_id).first()
        if not profile:
            return Response({"detail": "Staff profile not found."}, status=404)

        profile.is_active = False
        profile.disabled_at = timezone.now()
        profile.save(update_fields=["is_active","disabled_at"])
        try:
            profile.user.is_active = False
            profile.user.save(update_fields=["is_active"])
        except Exception:
            pass

        log_action(evt, request.user, "outreach.staff.disabled", {"profile_id": profile.id})
        return Response({"detail": "Staff disabled."})

    @action(detail=True, methods=["post"], url_path=r"staff/(?P<profile_id>[^/.]+)/enable")
    def staff_enable(self, request, pk=None, profile_id=None):
        evt = self.get_object()
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Cannot enable staff on a CLOSED outreach."}, status=400)

        profile = OutreachStaffProfile.objects.select_related("user").filter(outreach_event=evt, id=profile_id).first()
        if not profile:
            return Response({"detail": "Staff profile not found."}, status=404)

        profile.is_active = True
        profile.disabled_at = None
        profile.save(update_fields=["is_active","disabled_at"])
        try:
            profile.user.is_active = True
            profile.user.save(update_fields=["is_active"])
        except Exception:
            pass

        log_action(evt, request.user, "outreach.staff.enabled", {"profile_id": profile.id})
        return Response({"detail": "Staff enabled."})

    @action(detail=True, methods=["post"], url_path="staff/import-file")
    def staff_import_file(self, request, pk=None):
        """Bulk import staff from CSV/XLSX.

        Required columns: email, full_name
        Optional: role_template, all_sites, site_ids (comma-separated), permissions (comma-separated)
        """
        evt = self.get_object()
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)

        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "file is required"}, status=400)

        try:
            rows = read_tabular_file(f)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)

        created, updated, errors = 0, 0, []
        for idx, row in enumerate(rows, start=2):
            try:
                row = {str(k).strip().lower(): v for k, v in (row or {}).items()}
                email = (row.get("email") or "").strip().lower()
                phone = (row.get("phone") or "").strip()
                full_name = (row.get("full_name") or row.get("name") or "").strip()
                if not email or "@" not in email:
                    errors.append(f"Row {idx}: invalid email")
                    continue

                role_template = str(row.get("role_template") or "").strip()
                all_sites = str(row.get("all_sites") or "true").strip().lower() in ("1","true","yes","y")
                site_ids_raw = str(row.get("site_ids") or "").strip()
                perm_raw = str(row.get("permissions") or "").strip()

                site_ids = []
                if site_ids_raw:
                    site_ids = [int(x) for x in site_ids_raw.split(",") if str(x).strip().isdigit()]

                permissions = []
                if perm_raw:
                    permissions = [p.strip() for p in perm_raw.split(",") if p.strip()]

                # reuse same creation logic via serializer validation
                s = OutreachStaffCreateSerializer(data={
                    "email": email,
                    "phone": phone,
                    "full_name": full_name,
                    "role_template": role_template,
                    "permissions": permissions,
                    "all_sites": all_sites,
                    "site_ids": site_ids,
                })
                s.is_valid(raise_exception=True)
                account_role = s.get_account_role()

                user, is_new, raw_password = create_or_reset_outreach_user(email=email, full_name=full_name, role=account_role)

                profile, profile_created = OutreachStaffProfile.objects.get_or_create(
                    outreach_event=evt, user=user,
                    defaults={
                        "phone": phone,
                        "role_template": role_template,
                        "permissions": s.validated_data.get("permissions", []),
                        "all_sites": bool(all_sites),
                        "created_by": request.user,
                    }
                )
                if profile_created:
                    created += 1
                else:
                    updated += 1
                    profile.phone = phone
                    profile.role_template = role_template
                    profile.permissions = s.validated_data.get("permissions", [])
                    profile.all_sites = bool(all_sites)
                    profile.is_active = True
                    profile.disabled_at = None
                    profile.save(update_fields=["phone","role_template","permissions","all_sites","is_active","disabled_at"])

                if not profile.all_sites:
                    sites = OutreachSite.objects.filter(outreach_event=evt, id__in=site_ids)
                    profile.sites.set(sites)
                else:
                    profile.sites.clear()

            except Exception as e:
                errors.append(f"Row {idx}: {e}")

        log_action(evt, request.user, "outreach.staff.imported", {"created": created, "updated": updated, "errors": len(errors)})
        return Response({"created": created, "updated": updated, "errors": errors[:200]})

    # ---- Audit logs
    @action(detail=True, methods=["get"], url_path="audit-logs")
    def audit_logs(self, request, pk=None):
        evt = self.get_object()
        qs = OutreachAuditLog.objects.filter(outreach_event=evt).order_by("-created_at")[:500]
        return Response(OutreachAuditLogSerializer(qs, many=True).data)

    # ---- Reports / Exports
    @action(detail=True, methods=["post"], url_path="reports")
    def reports(self, request, pk=None):
        evt = self.get_object()
        export_type = (request.data.get("type") or request.data.get("export_type") or "summary").strip()
        export_format = (request.data.get("format") or "csv").strip().lower()
        filters = request.data.get("filters") or {}

        if export_format not in ("csv", "json", "pdf"):
            return Response({"detail": "format must be csv|json|pdf"}, status=400)

        export = OutreachExport.objects.create(
            outreach_event=evt,
            created_by=request.user,
            export_type=export_type,
            export_format=export_format,
            filters=filters if isinstance(filters, dict) else {},
        )

        # Build dataset
        payload = build_report_payload(evt, export_type, filters)

        if export_format == "json":
            # store as json file
            content = ContentFile((str(payload)).encode("utf-8"), name=f"outreach_{evt.id}_{export_type}.json")
            export.file.save(content.name, content, save=True)
        elif export_format == "csv":
            csv_bytes, filename = build_report_csv(evt, export_type, payload)
            export.file.save(filename, ContentFile(csv_bytes), save=True)
        else:
            # PDF (simple text-based)
            pdf_bytes, filename = build_report_pdf(evt, export_type, payload)
            export.file.save(filename, ContentFile(pdf_bytes), save=True)

        log_action(evt, request.user, "outreach.export.created", {"export_id": export.id, "type": export_type, "format": export_format})
        return Response(OutreachExportSerializer(export).data, status=201)

    @action(detail=True, methods=["get"], url_path=r"exports/(?P<export_id>[^/.]+)/download")
    def export_download(self, request, pk=None, export_id=None):
        evt = self.get_object()
        export = OutreachExport.objects.filter(outreach_event=evt, id=export_id).first()
        if not export or not export.file:
            return Response({"detail": "Export not found."}, status=404)
        try:
            return FileResponse(export.file.open("rb"), as_attachment=True, filename=export.file.name.split("/")[-1])
        except Exception:
            raise Http404("Export file missing")


# ------------------------
# Patients
# ------------------------

class OutreachPatientViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachPatientSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachPatient.objects.all().select_related("outreach_event", "site").order_by("-created_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        if is_outreach_super_admin(u):
            event_id = self.request.query_params.get("event_id")
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            if event_id:
                qs = qs.filter(outreach_event_id=event_id)
            return qs

        # staff must be scoped to event(s) and sites
        profiles = get_active_profiles(u)
        event_ids = list(profiles.values_list("outreach_event_id", flat=True))
        qs = qs.filter(outreach_event_id__in=event_ids)

        # site scope
        # If staff has multiple profiles, event_id is recommended.
        event_id = self.request.query_params.get("event_id")
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
            evt = OutreachEvent.objects.filter(id=event_id).first()
            if evt:
                ctx = _staff_sites_context(u, evt)
                if not ctx["all_sites"]:
                    qs = qs.filter(Q(site_id__in=ctx["site_ids"]) | Q(site__isnull=True))
        return qs

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)

        deny = _require_perm(request, evt, PERM_PATIENTS_CREATE)
        if deny: return deny

        ensure_outreach_writeable(evt)

        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)

        # determine site default for staff if not provided
        site = None
        site_id = s.validated_data.get("site")
        if site_id:
            site = OutreachSite.objects.filter(id=site_id.id, outreach_event=evt).first()
        if not site:
            ctx = _staff_sites_context(request.user, evt)
            if not ctx["all_sites"] and len(ctx["site_ids"]) == 1:
                site = OutreachSite.objects.filter(id=ctx["site_ids"][0], outreach_event=evt).first()

        if site and not _validate_patient_site(request, evt, site):
            return Response({"detail": "You are not assigned to this site."}, status=403)

        patient_code = allocate_next_patient_code(evt)

        # Duplicate warning
        full_name = s.validated_data.get("full_name", "").strip()
        phone = s.validated_data.get("phone", "").strip()
        warn = []
        if full_name:
            q = OutreachPatient.objects.filter(outreach_event=evt, full_name__iexact=full_name)
            if phone:
                q = q.filter(phone=phone)
            if q.exists():
                warn.append("Possible duplicate patient: same name (and phone if provided) already exists in this outreach.")

        patient = s.save(outreach_event=evt, site=site, patient_code=patient_code, created_by=request.user)

        log_action(evt, request.user, "outreach.patient.created", {"patient_id": patient.id, "patient_code": patient.patient_code})

        data = OutreachPatientSerializer(patient).data
        if warn:
            data["warnings"] = warn
        return Response(data, status=201)

    def update(self, request, *args, **kwargs):
        patient = self.get_object()
        evt = patient.outreach_event
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "This outreach is closed and is read-only."}, status=403)

        deny = _require_perm(request, evt, PERM_PATIENTS_EDIT)
        if deny: return deny

        # site access check
        if patient.site and not _validate_patient_site(request, evt, patient.site):
            return Response({"detail": "You are not assigned to this site."}, status=403)

        return super().update(request, *args, **kwargs)


# ------------------------
# Vitals
# ------------------------

class OutreachVitalsViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachVitalsSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachVitals.objects.all().select_related("outreach_event","patient").order_by("-recorded_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if is_outreach_super_admin(u):
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            if event_id:
                qs = qs.filter(outreach_event_id=event_id)
            return qs
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_VITALS)
        deny = _require_perm(request, evt, PERM_VITALS_CREATE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        patient = OutreachPatient.objects.filter(id=s.validated_data["patient"].id, outreach_event=evt).first()
        if not patient:
            return Response({"detail": "Invalid patient for this outreach."}, status=400)

        if patient.site and not _validate_patient_site(request, evt, patient.site):
            return Response({"detail": "You are not assigned to this site."}, status=403)

        obj = s.save(outreach_event=evt, recorded_by=request.user)
        log_action(evt, request.user, "outreach.vitals.created", {"vitals_id": obj.id, "patient_id": patient.id})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_VITALS)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_VITALS_EDIT)
        if deny: return deny
        return super().update(request, *args, **kwargs)


# ------------------------
# Encounter
# ------------------------

class OutreachEncounterViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachEncounterSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    queryset = OutreachEncounter.objects.all().select_related("outreach_event","patient").order_by("-recorded_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if is_outreach_super_admin(u):
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            if event_id:
                qs = qs.filter(outreach_event_id=event_id)
            return qs
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_ENCOUNTER)
        deny = _require_perm(request, evt, PERM_ENCOUNTER_CREATE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)

        patient = OutreachPatient.objects.filter(id=s.validated_data["patient"].id, outreach_event=evt).first()
        if not patient:
            return Response({"detail": "Invalid patient for this outreach."}, status=400)

        if patient.site and not _validate_patient_site(request, evt, patient.site):
            return Response({"detail": "You are not assigned to this site."}, status=403)

        obj = s.save(outreach_event=evt, recorded_by=request.user)
        log_action(evt, request.user, "outreach.encounter.created", {"encounter_id": obj.id, "patient_id": patient.id})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_ENCOUNTER)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_ENCOUNTER_EDIT)
        if deny: return deny
        return super().update(request, *args, **kwargs)


# ------------------------
# Lab Catalog, Orders, Results
# ------------------------

class OutreachLabTestViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachLabTestSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachLabTestCatalog.objects.all().select_related("outreach_event").order_by("name")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    def list(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_LAB)
        deny = _require_perm(request, evt, PERM_LAB_CATALOG_VIEW)
        if deny: return deny
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_LAB)
        deny = _require_perm(request, evt, PERM_LAB_CATALOG_MANAGE)
        if deny: return deny
        ensure_outreach_writeable(evt)
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        obj = s.save(outreach_event=evt, created_by=request.user)
        log_action(evt, request.user, "outreach.lab.catalog.created", {"test_id": obj.id, "code": obj.code})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_LAB)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_LAB_CATALOG_MANAGE)
        if deny: return deny
        return super().update(request, *args, **kwargs)

    @action(detail=False, methods=["post"], url_path="import-file")
    def import_file(self, request):
        """Import lab tests catalog from CSV/XLSX.

        Required columns: code, name
        Optional: unit, ref_low, ref_high, price, is_active
        """
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)

        ensure_module_enabled(evt, MODULE_LAB)
        deny = _require_perm(request, evt, PERM_LAB_CATALOG_MANAGE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "file is required"}, status=400)

        try:
            rows = read_tabular_file(f)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)

        if not rows:
            return Response({"detail": "File is empty."}, status=400)

        created, updated, errors = 0, 0, []
        for idx, row in enumerate(rows, start=2):
            try:
                row = {str(k).strip().lower(): v for k, v in (row or {}).items()}
                code = str(row.get("code") or "").strip()
                name = str(row.get("name") or "").strip()
                if not code or not name:
                    errors.append(f"Row {idx}: missing code or name")
                    continue

                def clean_value(val):
                    if val is None or val == "":
                        return None
                    if isinstance(val, float) and math.isnan(val):
                        return None
                    return val

                unit = str(clean_value(row.get("unit") or "") or "").strip()
                ref_low = str(clean_value(row.get("ref_low") or "") or "").strip()
                ref_high = str(clean_value(row.get("ref_high") or "") or "").strip()
                price = clean_value(row.get("price"))
                is_active = row.get("is_active", True)
                is_active = str(is_active).strip().lower() not in ("0", "false", "no", "n")

                defaults = dict(
                    name=name,
                    unit=unit,
                    ref_low=ref_low,
                    ref_high=ref_high,
                    price=price if price is not None and str(price).strip() != "" else None,
                    is_active=is_active,
                    created_by=request.user,
                )
                obj, created_flag = OutreachLabTestCatalog.objects.update_or_create(
                    outreach_event=evt,
                    code=code,
                    defaults=defaults,
                )
                if created_flag:
                    created += 1
                else:
                    updated += 1
            except Exception as e:
                errors.append(f"Row {idx}: {e}")

        log_action(evt, request.user, "outreach.lab.catalog.imported", {"created": created, "updated": updated, "errors": len(errors)})
        return Response({"created": created, "updated": updated, "errors": errors[:200]})


class OutreachLabOrderViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachLabOrderSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachLabOrder.objects.all().select_related("outreach_event","patient").prefetch_related("items","items__test").order_by("-ordered_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_LAB)
        deny = _require_perm(request, evt, PERM_LAB_ORDER_CREATE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        cs = OutreachLabOrderCreateSerializer(data=request.data)
        cs.is_valid(raise_exception=True)
        patient = OutreachPatient.objects.filter(id=cs.validated_data["patient_id"], outreach_event=evt).first()
        if not patient:
            return Response({"detail": "Invalid patient for this outreach."}, status=400)

        if patient.site and not _validate_patient_site(request, evt, patient.site):
            return Response({"detail": "You are not assigned to this site."}, status=403)

        test_ids = cs.validated_data["test_ids"]
        tests = list(OutreachLabTestCatalog.objects.filter(outreach_event=evt, id__in=test_ids, is_active=True))
        if not tests:
            return Response({"detail": "No valid lab tests selected."}, status=400)

        with transaction.atomic():
            order = OutreachLabOrder.objects.create(
                outreach_event=evt,
                patient=patient,
                status=LabOrderStatus.ORDERED,
                notes=cs.validated_data.get("notes",""),
                ordered_by=request.user,
            )
            for t in tests:
                OutreachLabOrderItem.objects.create(
                    lab_order=order,
                    test=t,
                    test_name=t.name,
                )

        log_action(evt, request.user, "outreach.lab.order.created", {"order_id": order.id, "patient_id": patient.id, "tests": [t.id for t in tests]})
        return Response(OutreachLabOrderSerializer(order).data, status=201)

    def update(self, request, *args, **kwargs):
        order = self.get_object()
        evt = order.outreach_event
        ensure_module_enabled(evt, MODULE_LAB)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_LAB_ORDER_EDIT)
        if deny: return deny
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="mark-collected")
    def mark_collected(self, request, pk=None):
        order = self.get_object()
        evt = order.outreach_event
        ensure_module_enabled(evt, MODULE_LAB)
        deny = _require_perm(request, evt, PERM_LAB_ORDER_EDIT)
        if deny: return deny
        ensure_outreach_writeable(evt)

        order.status = LabOrderStatus.COLLECTED
        order.collected_at = timezone.now()
        order.save(update_fields=["status","collected_at"])
        log_action(evt, request.user, "outreach.lab.order.collected", {"order_id": order.id})
        return Response(OutreachLabOrderSerializer(order).data)

    @action(detail=True, methods=["post"], url_path="mark-result-ready")
    def mark_result_ready(self, request, pk=None):
        order = self.get_object()
        evt = order.outreach_event
        ensure_module_enabled(evt, MODULE_LAB)
        deny = _require_perm(request, evt, PERM_LAB_ORDER_EDIT)
        if deny: return deny
        ensure_outreach_writeable(evt)

        order.status = LabOrderStatus.RESULT_READY
        order.result_ready_at = timezone.now()
        order.save(update_fields=["status","result_ready_at"])
        log_action(evt, request.user, "outreach.lab.order.result_ready", {"order_id": order.id})
        return Response(OutreachLabOrderSerializer(order).data)


class OutreachLabResultViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachLabResultSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    queryset = OutreachLabResult.objects.all().select_related("outreach_event","lab_order").order_by("-recorded_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        order_id = self.request.query_params.get("lab_order_id")
        patient_id = self.request.query_params.get("patient_id")
        if order_id:
            qs = qs.filter(lab_order_id=order_id)
        
        if patient_id:
            qs = qs.filter(lab_order__patient_id=patient_id)
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    @action(detail=True, methods=["get"], url_path="attachment")
    def attachment(self, request, pk=None):
        """Download/view the uploaded attachment for a lab result (if any).

        This route exists so the Next.js proxy (/api/proxy) can forward the file under /api.
        """
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_LAB)

        # basic permission gate: any lab-related capability should be enough to view the attachment
        if not (
            is_outreach_super_admin(request.user)
            or has_outreach_permission(request.user, evt, PERM_LAB_ORDER_CREATE)
            or has_outreach_permission(request.user, evt, PERM_LAB_ORDER_EDIT)
            or has_outreach_permission(request.user, evt, PERM_LAB_RESULT_CREATE)
            or has_outreach_permission(request.user, evt, PERM_LAB_RESULT_EDIT)
            or has_outreach_permission(request.user, evt, PERM_LAB_CATALOG_VIEW)
            or has_outreach_permission(request.user, evt, PERM_LAB_CATALOG_MANAGE)
        ):
            return Response({"detail": "Permission denied."}, status=403)

        if not obj.result_attachment:
            return Response({"detail": "No attachment for this result."}, status=404)

        f = obj.result_attachment
        filename = (getattr(f, 'name', '') or '').split('/')[-1] or f"result_{obj.id}"
        resp = FileResponse(f.open('rb'), as_attachment=True, filename=filename)
        return resp


    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_LAB)
        deny = _require_perm(request, evt, PERM_LAB_RESULT_CREATE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        order = OutreachLabOrder.objects.filter(id=s.validated_data["lab_order"].id, outreach_event=evt).first()
        if not order:
            return Response({"detail": "Invalid lab order for this outreach."}, status=400)

        obj = s.save(outreach_event=evt, recorded_by=request.user)
        # keep order status updated
        if order.status != LabOrderStatus.RESULT_READY:
            order.status = LabOrderStatus.RESULT_READY
            order.result_ready_at = timezone.now()
            order.save(update_fields=["status","result_ready_at"])

        log_action(evt, request.user, "outreach.lab.result.created", {"result_id": obj.id, "order_id": order.id})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_LAB)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_LAB_RESULT_EDIT)
        if deny: return deny
        return super().update(request, *args, **kwargs)


# ------------------------
# Pharmacy catalog + dispense
# ------------------------

class OutreachDrugViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachDrugSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachDrugCatalog.objects.all().select_related("outreach_event").order_by("name")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    def list(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_PHARMACY)
        deny = _require_perm(request, evt, PERM_PHARMACY_CATALOG_VIEW)
        if deny: return deny
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_PHARMACY)
        deny = _require_perm(request, evt, PERM_PHARMACY_CATALOG_MANAGE)
        if deny: return deny
        ensure_outreach_writeable(evt)
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        obj = s.save(outreach_event=evt, created_by=request.user)
        log_action(evt, request.user, "outreach.pharmacy.catalog.created", {"drug_id": obj.id, "code": obj.code})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_PHARMACY)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_PHARMACY_CATALOG_MANAGE)
        if deny: return deny
        return super().update(request, *args, **kwargs)

    @action(detail=False, methods=["post"], url_path="import-file")
    def import_file(self, request):
        """Import pharmacy catalog from CSV/XLSX.

        Required columns: code, name
        Optional: strength, form, route, qty_per_unit, unit_price, is_active
        """
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)

        ensure_module_enabled(evt, MODULE_PHARMACY)
        deny = _require_perm(request, evt, PERM_PHARMACY_CATALOG_MANAGE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "file is required"}, status=400)

        try:
            rows = read_tabular_file(f)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)

        if not rows:
            return Response({"detail": "File is empty."}, status=400)

        created, updated, errors = 0, 0, []
        for idx, row in enumerate(rows, start=2):
            try:
                row = {str(k).strip().lower(): v for k, v in (row or {}).items()}
                code = str(row.get("code") or "").strip()
                name = str(row.get("name") or "").strip()
                if not code or not name:
                    errors.append(f"Row {idx}: missing code or name")
                    continue

                def clean_value(val):
                    if val is None or val == "":
                        return None
                    if isinstance(val, float) and math.isnan(val):
                        return None
                    return val

                strength = str(clean_value(row.get("strength") or "") or "").strip()
                form = str(clean_value(row.get("form") or "") or "").strip()
                route = str(clean_value(row.get("route") or "") or "").strip()
                qty_per_unit = clean_value(row.get("qty_per_unit"))
                unit_price = clean_value(row.get("unit_price") or row.get("price"))
                is_active = row.get("is_active", True)
                is_active = str(is_active).strip().lower() not in ("0", "false", "no", "n")

                defaults = dict(
                    name=name,
                    strength=strength,
                    form=form,
                    route=route,
                    qty_per_unit=qty_per_unit if qty_per_unit is not None and str(qty_per_unit).strip() != "" else None,
                    unit_price=unit_price if unit_price is not None and str(unit_price).strip() != "" else None,
                    is_active=is_active,
                    created_by=request.user,
                )
                obj, created_flag = OutreachDrugCatalog.objects.update_or_create(
                    outreach_event=evt,
                    code=code,
                    defaults=defaults,
                )
                if created_flag:
                    created += 1
                else:
                    updated += 1
            except Exception as e:
                errors.append(f"Row {idx}: {e}")

        log_action(evt, request.user, "outreach.pharmacy.catalog.imported", {"created": created, "updated": updated, "errors": len(errors)})
        return Response({"created": created, "updated": updated, "errors": errors[:200]})


class OutreachDispenseViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachDispenseSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachDispense.objects.all().select_related("outreach_event","patient","drug").order_by("-dispensed_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_PHARMACY)
        deny = _require_perm(request, evt, PERM_PHARMACY_DISPENSE_CREATE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        cs = OutreachDispenseCreateSerializer(data=request.data)
        cs.is_valid(raise_exception=True)

        patient = OutreachPatient.objects.filter(id=cs.validated_data["patient_id"], outreach_event=evt).first()
        if not patient:
            return Response({"detail": "Invalid patient for this outreach."}, status=400)

        if patient.site and not _validate_patient_site(request, evt, patient.site):
            return Response({"detail": "You are not assigned to this site."}, status=403)

        drug = None
        drug_name = cs.validated_data.get("drug_name","").strip()
        strength = cs.validated_data.get("strength","").strip()
        if cs.validated_data.get("drug_id"):
            drug = OutreachDrugCatalog.objects.filter(id=cs.validated_data["drug_id"], outreach_event=evt).first()
            if drug:
                drug_name = drug.name
                strength = drug.strength

        if not drug_name:
            return Response({"detail": "drug_id or drug_name is required"}, status=400)

        obj = OutreachDispense.objects.create(
            outreach_event=evt,
            patient=patient,
            drug=drug,
            drug_name=drug_name,
            strength=strength,
            quantity=cs.validated_data["quantity"],
            instruction=cs.validated_data.get("instruction",""),
            dispensed_by=request.user,
        )
        log_action(evt, request.user, "outreach.pharmacy.dispense.created", {"dispense_id": obj.id, "patient_id": patient.id})
        return Response(OutreachDispenseSerializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_PHARMACY)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_PHARMACY_DISPENSE_EDIT)
        if deny: return deny
        return super().update(request, *args, **kwargs)


# ------------------------
# Immunization, Blood donation, Counseling, Maternal
# ------------------------


class OutreachVaccineCatalogViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachVaccineCatalogSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachVaccineCatalog.objects.all().select_related("outreach_event").order_by("name")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        # hide inactive by default unless requested
        if self.request.query_params.get("include_inactive") not in ("1", "true", "True"):
            qs = qs.filter(is_active=True)
        return qs

    def list(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=True)
        except Exception:
            evt = None
        if evt:
            ensure_module_enabled(evt, MODULE_IMMUNIZATION)
            deny = _require_perm(request, evt, PERM_IMMUNIZATION_CREATE)  # treat as catalog view
            if deny:
                return deny
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_IMMUNIZATION)
        deny = _require_perm(request, evt, PERM_IMMUNIZATION_EDIT)  # treat as catalog manage
        if deny:
            return deny
        ensure_outreach_writeable(evt)

        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        obj = s.save(outreach_event=evt, created_by=request.user)
        log_action(evt, request.user, "outreach.immunization.catalog.created", {"id": obj.id, "name": obj.name})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_IMMUNIZATION)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_IMMUNIZATION_EDIT)
        if deny:
            return deny
        return super().update(request, *args, **kwargs)

    @action(detail=False, methods=["post"], url_path="import-file")
    def import_file(self, request, *args, **kwargs):
        """Import vaccines from CSV/XLSX with columns: name (required), code, manufacturer, notes, is_active."""
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_IMMUNIZATION)
        deny = _require_perm(request, evt, PERM_IMMUNIZATION_EDIT)
        if deny:
            return deny
        ensure_outreach_writeable(evt)

        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "file is required."}, status=400)

        try:
            rows = read_tabular_file(f)
        except Exception as e:
            return Response({"detail": f"Import failed: {str(e)}"}, status=400)

        created = 0
        updated = 0
        errors = []

        for idx, r in enumerate(rows, start=1):
            name = str(r.get("name") or r.get("vaccine") or "").strip()
            if not name:
                errors.append({"row": idx, "error": "Missing name"})
                continue
            code = str(r.get("code") or "").strip()
            manufacturer = str(r.get("manufacturer") or r.get("brand") or "").strip()
            notes = str(r.get("notes") or "").strip()
            is_active = r.get("is_active")
            if isinstance(is_active, str):
                is_active = is_active.strip().lower() in ("1", "true", "yes", "y")
            if is_active is None:
                is_active = True

            obj = OutreachVaccineCatalog.objects.filter(outreach_event=evt, name__iexact=name).first()
            if obj:
                obj.code = code or obj.code
                obj.manufacturer = manufacturer
                obj.notes = notes
                obj.is_active = bool(is_active)
                obj.save(update_fields=["code","manufacturer","notes","is_active"])
                updated += 1
            else:
                OutreachVaccineCatalog.objects.create(
                    outreach_event=evt,
                    name=name,
                    code=code,
                    manufacturer=manufacturer,
                    notes=notes,
                    is_active=bool(is_active),
                    created_by=request.user,
                )
                created += 1

        log_action(evt, request.user, "outreach.immunization.catalog.imported", {"created": created, "updated": updated, "errors": len(errors)})
        return Response({"created": created, "updated": updated, "errors": errors})


class OutreachImmunizationViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachImmunizationSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachImmunization.objects.all().select_related("outreach_event","patient").order_by("-administered_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_IMMUNIZATION)
        deny = _require_perm(request, evt, PERM_IMMUNIZATION_CREATE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        patient = OutreachPatient.objects.filter(id=s.validated_data["patient"].id, outreach_event=evt).first()
        if not patient:
            return Response({"detail": "Invalid patient for this outreach."}, status=400)

        obj = s.save(outreach_event=evt, administered_by=request.user)
        log_action(evt, request.user, "outreach.immunization.created", {"id": obj.id, "patient_id": patient.id})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_IMMUNIZATION)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_IMMUNIZATION_EDIT)
        if deny: return deny
        return super().update(request, *args, **kwargs)


class OutreachBloodDonationViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachBloodDonationSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachBloodDonation.objects.all().select_related("outreach_event","patient").order_by("-recorded_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_BLOOD_DONATION)
        deny = _require_perm(request, evt, PERM_BLOOD_CREATE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        patient = None
        if s.validated_data.get("patient"):
            patient = OutreachPatient.objects.filter(id=s.validated_data["patient"].id, outreach_event=evt).first()
            if not patient:
                return Response({"detail": "Invalid patient for this outreach."}, status=400)

        obj = s.save(outreach_event=evt, recorded_by=request.user)
        log_action(evt, request.user, "outreach.blood_donation.created", {"id": obj.id})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_BLOOD_DONATION)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_BLOOD_EDIT)
        if deny: return deny
        return super().update(request, *args, **kwargs)


class OutreachCounselingViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachCounselingSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachCounseling.objects.all().select_related("outreach_event","patient","counselor").order_by("-recorded_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)

        profiles = get_active_profiles(u)
        event_ids = list(profiles.values_list("outreach_event_id", flat=True))
        qs = qs.filter(outreach_event_id__in=event_ids)
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)

        # confidentiality: PRIVATE visible only to counselor and super admin
        # INTERNAL visible to staff with view_sensitive permission too
        visible = Q(visibility_level=CounselingVisibility.INTERNAL)
        visible |= Q(counselor=u)
        qs = qs.filter(visible)
        return qs

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_COUNSELING)

        if obj.visibility_level == CounselingVisibility.PRIVATE and not (is_outreach_super_admin(request.user) or obj.counselor_id == request.user.id):
            return Response({"detail": "Not allowed to view this counseling note."}, status=403)

        if obj.visibility_level == CounselingVisibility.INTERNAL:
            # require view_sensitive or creator
            if not (is_outreach_super_admin(request.user) or obj.counselor_id == request.user.id or has_outreach_permission(request.user, evt, PERM_COUNSELING_VIEW_SENSITIVE)):
                return Response({"detail": "Not allowed to view this counseling note."}, status=403)

        return super().retrieve(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_COUNSELING)
        deny = _require_perm(request, evt, PERM_COUNSELING_CREATE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        patient = OutreachPatient.objects.filter(id=s.validated_data["patient"].id, outreach_event=evt).first()
        if not patient:
            return Response({"detail": "Invalid patient for this outreach."}, status=400)

        obj = s.save(outreach_event=evt, counselor=request.user)
        log_action(evt, request.user, "outreach.counseling.created", {"id": obj.id, "patient_id": patient.id, "visibility": obj.visibility_level})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_COUNSELING)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        # PRIVATE can be edited by counselor or OSA; INTERNAL by permission
        if obj.counselor_id != request.user.id and not is_outreach_super_admin(request.user):
            deny = _require_perm(request, evt, PERM_COUNSELING_EDIT)
            if deny: return deny
        return super().update(request, *args, **kwargs)


class OutreachMaternalViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachMaternalSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachMaternal.objects.all().select_related("outreach_event","patient").order_by("-recorded_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_MATERNAL)
        deny = _require_perm(request, evt, PERM_MATERNAL_CREATE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        patient = OutreachPatient.objects.filter(id=s.validated_data["patient"].id, outreach_event=evt).first()
        if not patient:
            return Response({"detail": "Invalid patient for this outreach."}, status=400)

        obj = s.save(outreach_event=evt, recorded_by=request.user)
        log_action(evt, request.user, "outreach.maternal.created", {"id": obj.id, "patient_id": patient.id})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_MATERNAL)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_MATERNAL_EDIT)
        if deny: return deny
        return super().update(request, *args, **kwargs)


# ------------------------
# Referral / Surgicals / Eye checks / Dental checks
# ------------------------

class OutreachReferralViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachReferralSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachReferral.objects.all().select_related("outreach_event","patient").order_by("-recorded_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_REFERRAL)
        deny = _require_perm(request, evt, PERM_REFERRAL_CREATE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        patient = OutreachPatient.objects.filter(id=s.validated_data["patient"].id, outreach_event=evt).first()
        if not patient:
            return Response({"detail": "Invalid patient for this outreach."}, status=400)
        if patient.site and not _validate_patient_site(request, evt, patient.site):
            return Response({"detail": "You are not assigned to this site."}, status=403)

        obj = s.save(outreach_event=evt, recorded_by=request.user)
        log_action(evt, request.user, "outreach.referral.created", {"id": obj.id, "patient_id": patient.id})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_REFERRAL)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_REFERRAL_EDIT)
        if deny: return deny
        return super().update(request, *args, **kwargs)


class OutreachSurgicalViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachSurgicalSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachSurgical.objects.all().select_related("outreach_event","patient").order_by("-recorded_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_SURGICALS)
        deny = _require_perm(request, evt, PERM_SURGICALS_CREATE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        patient = OutreachPatient.objects.filter(id=s.validated_data["patient"].id, outreach_event=evt).first()
        if not patient:
            return Response({"detail": "Invalid patient for this outreach."}, status=400)
        if patient.site and not _validate_patient_site(request, evt, patient.site):
            return Response({"detail": "You are not assigned to this site."}, status=403)

        obj = s.save(outreach_event=evt, recorded_by=request.user)
        log_action(evt, request.user, "outreach.surgicals.created", {"id": obj.id, "patient_id": patient.id})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_SURGICALS)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_SURGICALS_EDIT)
        if deny: return deny
        return super().update(request, *args, **kwargs)


class OutreachEyeCheckViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachEyeCheckSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachEyeCheck.objects.all().select_related("outreach_event","patient").order_by("-recorded_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_EYE_CHECKS)
        deny = _require_perm(request, evt, PERM_EYE_CHECKS_CREATE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        patient = OutreachPatient.objects.filter(id=s.validated_data["patient"].id, outreach_event=evt).first()
        if not patient:
            return Response({"detail": "Invalid patient for this outreach."}, status=400)
        if patient.site and not _validate_patient_site(request, evt, patient.site):
            return Response({"detail": "You are not assigned to this site."}, status=403)

        obj = s.save(outreach_event=evt, recorded_by=request.user)
        log_action(evt, request.user, "outreach.eye_checks.created", {"id": obj.id, "patient_id": patient.id})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_EYE_CHECKS)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_EYE_CHECKS_EDIT)
        if deny: return deny
        return super().update(request, *args, **kwargs)


class OutreachDentalCheckViewSet(viewsets.ModelViewSet):
    serializer_class = OutreachDentalCheckSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachStaff]
    queryset = OutreachDentalCheck.objects.all().select_related("outreach_event","patient").order_by("-recorded_at")

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        event_id = self.request.query_params.get("event_id")
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if is_outreach_super_admin(u):
            if not event_id:
                return qs.none()
            if not _is_platform_admin(u):
                qs = qs.filter(outreach_event__created_by=u)
            return qs.filter(outreach_event_id=event_id)
        profiles = get_active_profiles(u)
        qs = qs.filter(outreach_event_id__in=list(profiles.values_list("outreach_event_id", flat=True)))
        if event_id:
            qs = qs.filter(outreach_event_id=event_id)
        return qs

    def create(self, request, *args, **kwargs):
        try:
            evt = _pick_event_for_request(request, allow_closed=False)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)
        ensure_module_enabled(evt, MODULE_DENTAL_CHECKS)
        deny = _require_perm(request, evt, PERM_DENTAL_CHECKS_CREATE)
        if deny: return deny
        ensure_outreach_writeable(evt)

        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        patient = OutreachPatient.objects.filter(id=s.validated_data["patient"].id, outreach_event=evt).first()
        if not patient:
            return Response({"detail": "Invalid patient for this outreach."}, status=400)
        if patient.site and not _validate_patient_site(request, evt, patient.site):
            return Response({"detail": "You are not assigned to this site."}, status=403)

        obj = s.save(outreach_event=evt, recorded_by=request.user)
        log_action(evt, request.user, "outreach.dental_checks.created", {"id": obj.id, "patient_id": patient.id})
        return Response(self.get_serializer(obj).data, status=201)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        evt = obj.outreach_event
        ensure_module_enabled(evt, MODULE_DENTAL_CHECKS)
        if evt.status == OutreachStatus.CLOSED:
            return Response({"detail": "Closed outreach is read-only."}, status=403)
        deny = _require_perm(request, evt, PERM_DENTAL_CHECKS_EDIT)
        if deny: return deny
        return super().update(request, *args, **kwargs)


# ------------------------
# Exports ViewSet (read-only list/retrieve)
# ------------------------

class OutreachExportViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OutreachExportSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsOutreachSuperAdmin]

    def get_queryset(self):
        qs = super().get_queryset()
        u = getattr(self.request, 'user', None)
        if not u:
            return qs.none()
        if _is_platform_admin(u):
            return qs
        return qs.filter(outreach_event__created_by=u)

    queryset = OutreachExport.objects.all().select_related("outreach_event").order_by("-created_at")


# ------------------------
# Reporting helpers
# ------------------------

def build_report_payload(evt: OutreachEvent, export_type: str, filters: dict):
    export_type = (export_type or "summary").strip().lower()

    if export_type == "summary":
        return {
            "event": {"id": evt.id, "title": evt.title, "status": evt.status, "starts_at": evt.starts_at, "ends_at": evt.ends_at, "closed_at": evt.closed_at},
            "counts": {
                "sites": evt.sites.count(),
                "staff": evt.staff_profiles.count(),
                "patients": evt.patients.count(),
                "vitals": evt.vitals.count(),
                "encounters": evt.encounters.count(),
                "lab_orders": evt.lab_orders.count(),
                "lab_results": evt.lab_results.count(),
                "dispenses": evt.dispenses.count(),
                "immunizations": evt.immunizations.count(),
                "blood_donations": evt.blood_donations.count(),
                "counseling_sessions": evt.counseling_sessions.count(),
                "maternal_records": evt.maternal_records.count(),
                "referrals": evt.referrals.count(),
                "surgicals": evt.surgicals.count(),
                "eye_checks": evt.eye_checks.count(),
                "dental_checks": evt.dental_checks.count(),
            },
            "demographics": {
                "sex": list(evt.patients.values("sex").annotate(count=Count("id")).order_by("-count")),
            },
        }

    if export_type == "patients":
        qs = evt.patients.select_related("site").order_by("patient_code")
        return [
            {
                "patient_code": p.patient_code,
                "full_name": p.full_name,
                "sex": p.sex,
                "age_years": p.age_years,
                "phone": p.phone,
                "email": p.email,
                "site": p.site.name if p.site else "",
                "created_at": p.created_at,
            }
            for p in qs
        ]

    if export_type == "vitals":
        qs = evt.vitals.select_related("patient").order_by("-recorded_at")
        return [
            {
                "patient_code": v.patient.patient_code,
                "full_name": v.patient.full_name,
                "bp_sys": v.bp_sys,
                "bp_dia": v.bp_dia,
                "pulse": v.pulse,
                "temp_c": v.temp_c,
                "weight_kg": v.weight_kg,
                "height_cm": v.height_cm,
                "bmi": v.bmi,
                "recorded_at": v.recorded_at,
            }
            for v in qs
        ]

    if export_type == "encounters":
        qs = evt.encounters.select_related("patient").order_by("-recorded_at")
        return [
            {
                "patient_code": e.patient.patient_code,
                "full_name": e.patient.full_name,
                "complaint": e.complaint,
                "diagnosis_tags": e.diagnosis_tags,
                "plan": e.plan,
                "recorded_at": e.recorded_at,
            }
            for e in qs
        ]

    if export_type == "lab_orders":
        qs = evt.lab_orders.select_related("patient").prefetch_related("items").order_by("-ordered_at")
        return [
            {
                "order_id": o.id,
                "patient_code": o.patient.patient_code,
                "full_name": o.patient.full_name,
                "status": o.status,
                "tests": [i.test_name for i in o.items.all()],
                "ordered_at": o.ordered_at,
            }
            for o in qs
        ]

    if export_type == "lab_results":
        qs = evt.lab_results.select_related("lab_order","lab_order__patient").order_by("-recorded_at")
        return [
            {
                "order_id": r.lab_order_id,
                "patient_code": r.lab_order.patient.patient_code,
                "full_name": r.lab_order.patient.full_name,
                "test_name": r.test_name,
                "result_value": r.result_value,
                "unit": r.unit,
                "recorded_at": r.recorded_at,
            }
            for r in qs
        ]

    if export_type == "dispenses":
        qs = evt.dispenses.select_related("patient").order_by("-dispensed_at")
        return [
            {
                "patient_code": d.patient.patient_code,
                "full_name": d.patient.full_name,
                "drug_name": d.drug_name,
                "strength": d.strength,
                "quantity": d.quantity,
                "instruction": d.instruction,
                "dispensed_at": d.dispensed_at,
            }
            for d in qs
        ]

    if export_type == "immunizations":
        qs = evt.immunizations.select_related("patient").order_by("-administered_at")
        return [
            {
                "patient_code": i.patient.patient_code,
                "full_name": i.patient.full_name,
                "vaccine_name": i.vaccine_name,
                "dose_number": i.dose_number,
                "batch_number": i.batch_number,
                "route": i.route,
                "administered_at": i.administered_at,
            }
            for i in qs
        ]

    if export_type == "blood_donations":
        qs = evt.blood_donations.select_related("patient").order_by("-recorded_at")
        return [
            {
                "patient_code": b.patient.patient_code if b.patient else "",
                "full_name": b.patient.full_name if b.patient else "",
                "eligibility_status": b.eligibility_status,
                "outcome": b.outcome,
                "deferral_reason": b.deferral_reason,
                "recorded_at": b.recorded_at,
            }
            for b in qs
        ]

    if export_type == "counseling":
        qs = evt.counseling_sessions.select_related("patient").order_by("-recorded_at")
        return [
            {
                "patient_code": c.patient.patient_code,
                "full_name": c.patient.full_name,
                "topics": c.topics,
                "duration_minutes": c.duration_minutes,
                "visibility_level": c.visibility_level,
                "recorded_at": c.recorded_at,
            }
            for c in qs
        ]

    if export_type == "maternal":
        qs = evt.maternal_records.select_related("patient").order_by("-recorded_at")
        return [
            {
                "patient_code": m.patient.patient_code,
                "full_name": m.patient.full_name,
                "pregnancy_status": m.pregnancy_status,
                "gestational_age_weeks": m.gestational_age_weeks,
                "risk_flags": m.risk_flags,
                "recorded_at": m.recorded_at,
            }
            for m in qs
        ]

    if export_type == "audit_logs":
        qs = evt.audit_logs.select_related("actor").order_by("-created_at")
        return [
            {
                "created_at": a.created_at,
                "actor": getattr(a.actor, "email", None),
                "action": a.action,
                "meta": a.meta,
            }
            for a in qs
        ]

    # fallback summary
    return build_report_payload(evt, "summary", filters)


def build_report_csv(evt: OutreachEvent, export_type: str, payload):
    export_type = (export_type or "summary").strip().lower()
    filename = f"outreach_{evt.id}_{export_type}.csv"

    if export_type == "summary":
        headers = ["metric", "value"]
        rows = []
        for k, v in payload.get("counts", {}).items():
            rows.append([k, v])
        return _csv_bytes(headers, rows), filename

    if isinstance(payload, list) and payload:
        headers = list(payload[0].keys())
        rows = []
        for item in payload:
            rows.append([item.get(h, "") for h in headers])
        return _csv_bytes(headers, rows), filename

    # empty dataset
    return _csv_bytes(["detail"], [["No data"]]), filename


def build_report_pdf(evt: OutreachEvent, export_type: str, payload):
    """Generate a minimal PDF using reportlab if available.

    We keep it simple to avoid extra template complexity.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except Exception:
        # fallback as text bytes
        text = f"Outreach {evt.title} - {export_type}\n\n{payload}"
        return text.encode("utf-8"), f"outreach_{evt.id}_{export_type}.txt"

    filename = f"outreach_{evt.id}_{export_type}.pdf"
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    y = height - 40
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, f"Outreach Report: {evt.title}")
    y -= 20
    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Type: {export_type} | Status: {evt.status} | Generated: {timezone.now().isoformat(timespec='seconds')}")
    y -= 25

    def write_line(line):
        nonlocal y
        if y < 60:
            c.showPage()
            y = height - 40
            c.setFont("Helvetica", 10)
        c.drawString(40, y, line[:110])
        y -= 14

    if isinstance(payload, dict):
        for k, v in payload.items():
            write_line(f"{k}: {v}")
    elif isinstance(payload, list):
        for item in payload[:200]:
            write_line(str(item))
    else:
        write_line(str(payload))

    c.showPage()
    c.save()
    return buf.getvalue(), filename
