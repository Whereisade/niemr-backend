from __future__ import annotations

import csv
import io
import json
import math
from collections import Counter, defaultdict
from datetime import datetime

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q, Count
from django.http import FileResponse, Http404
from django.template.loader import render_to_string
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
            content = ContentFile(json.dumps(payload, default=str, indent=2).encode("utf-8"), name=f"outreach_{evt.id}_{export_type}.json")
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

    
    @action(detail=True, methods=["get"], url_path="insights")
    def insights(self, request, pk=None):
        """Analytics payload for the Insights tab (JSON).

        Optional query params:
        - site_id: filter by patient.site
        - from: YYYY-MM-DD (inclusive)
        - to: YYYY-MM-DD (inclusive)
        """
        evt = self.get_object()
        filters = {}
        site_id = request.query_params.get("site_id")
        if site_id not in (None, "", "null", "None"):
            filters["site_id"] = site_id
        f = request.query_params.get("from") or request.query_params.get("start") or request.query_params.get("start_date")
        t = request.query_params.get("to") or request.query_params.get("end") or request.query_params.get("end_date")
        if f:
            filters["from"] = f
        if t:
            filters["to"] = t

        payload = build_insights_payload(evt, filters)
        return Response(payload)

    @action(detail=True, methods=["get"], url_path=r"exports/(?P<export_id>[^/.]+)/download")
    def export_download(self, request, pk=None, export_id=None):
        evt = self.get_object()
        export = OutreachExport.objects.filter(outreach_event=evt, id=export_id).first()
        if not export or not export.file:
            return Response({"detail": "Export not found."}, status=404)
        try:
            inline = request.query_params.get("inline") in ("1","true","yes")
            as_attach = not (inline and export.export_format == "pdf")
            return FileResponse(export.file.open("rb"), as_attachment=as_attach, filename=export.file.name.split("/")[-1])
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

            q = (self.request.query_params.get("q") or "").strip()
            if q:
                qs = qs.filter(
                    Q(full_name__icontains=q)
                    | Q(patient_code__icontains=q)
                    | Q(phone__icontains=q)
                    | Q(email__icontains=q)
                )
            limit = self.request.query_params.get("limit")
            if limit and str(limit).isdigit():
                qs = qs[: int(limit)]
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

        q = (self.request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(full_name__icontains=q)
                | Q(patient_code__icontains=q)
                | Q(phone__icontains=q)
                | Q(email__icontains=q)
            )
        limit = self.request.query_params.get("limit")
        if limit and str(limit).isdigit():
            qs = qs[: int(limit)]
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

            q = (self.request.query_params.get("q") or "").strip()
            if q:
                qs = qs.filter(
                    Q(full_name__icontains=q)
                    | Q(patient_code__icontains=q)
                    | Q(phone__icontains=q)
                    | Q(email__icontains=q)
                )
            limit = self.request.query_params.get("limit")
            if limit and str(limit).isdigit():
                qs = qs[: int(limit)]
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

            q = (self.request.query_params.get("q") or "").strip()
            if q:
                qs = qs.filter(
                    Q(full_name__icontains=q)
                    | Q(patient_code__icontains=q)
                    | Q(phone__icontains=q)
                    | Q(email__icontains=q)
                )
            limit = self.request.query_params.get("limit")
            if limit and str(limit).isdigit():
                qs = qs[: int(limit)]
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


def _parse_date_like(value: str):
    if not value:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("null","none"):
        return None
    # Accept YYYY-MM-DD or full ISO datetime
    try:
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return datetime.strptime(s, "%Y-%m-%d").date()
        # fallback: try ISO
        return datetime.fromisoformat(s.replace("Z","+00:00")).date()
    except Exception:
        return None


def _apply_patient_site_filter(qs, filters: dict):
    site_id = filters.get("site_id")
    if site_id in (None, "", "null", "None"):
        return qs
    try:
        return qs.filter(patient__site_id=site_id)
    except Exception:
        # for patient qs itself
        return qs.filter(site_id=site_id)


def _date_range(filters: dict):
    start = _parse_date_like(filters.get("from") or filters.get("start") or filters.get("start_date"))
    end = _parse_date_like(filters.get("to") or filters.get("end") or filters.get("end_date"))
    return start, end


def _apply_date_filter(qs, field: str, filters: dict):
    start, end = _date_range(filters)
    if start:
        qs = qs.filter(**{f"{field}__date__gte": start})
    if end:
        qs = qs.filter(**{f"{field}__date__lte": end})
    return qs


def _age_band(age: int):
    try:
        a = int(age)
    except Exception:
        return None
    if a < 0:
        return None
    if a <= 4:
        return "0-4"
    if a <= 9:
        return "5-9"
    if a <= 14:
        return "10-14"
    if a <= 19:
        return "15-19"
    if a <= 24:
        return "20-24"
    if a <= 34:
        return "25-34"
    if a <= 44:
        return "35-44"
    if a <= 54:
        return "45-54"
    if a <= 64:
        return "55-64"
    return "65+"


def build_insights_payload(evt: OutreachEvent, filters: dict):
    """Compute analytics for Insights and Executive Summary PDFs."""
    filters = filters if isinstance(filters, dict) else {}

    # Patients (registered)
    p_qs = evt.patients.all().select_related("site")
    start, end = _date_range(filters)
    if start:
        p_qs = p_qs.filter(created_at__date__gte=start)
    if end:
        p_qs = p_qs.filter(created_at__date__lte=end)
    if filters.get("site_id") not in (None,"","null","None"):
        p_qs = p_qs.filter(site_id=filters.get("site_id"))

    patients_registered = p_qs.count()

    # Build activity patient ids across modules
    def ids_from(qs, field="patient_id"):
        return set(qs.values_list(field, flat=True).distinct())

    seen_ids = set()

    vitals_qs = _apply_date_filter(evt.vitals.all(), "recorded_at", filters)
    if filters.get("site_id"): vitals_qs = _apply_patient_site_filter(vitals_qs, filters)
    seen_ids |= ids_from(vitals_qs)

    enc_qs = _apply_date_filter(evt.encounters.all(), "recorded_at", filters)
    if filters.get("site_id"): enc_qs = _apply_patient_site_filter(enc_qs, filters)
    seen_ids |= ids_from(enc_qs)

    lab_orders_qs = _apply_date_filter(evt.lab_orders.all(), "ordered_at", filters)
    if filters.get("site_id"): lab_orders_qs = _apply_patient_site_filter(lab_orders_qs, filters)
    seen_ids |= ids_from(lab_orders_qs)

    lab_results_qs = _apply_date_filter(evt.lab_results.all(), "recorded_at", filters)
    if filters.get("site_id"):
        lab_results_qs = lab_results_qs.filter(lab_order__patient__site_id=filters.get("site_id"))
    seen_ids |= set(lab_results_qs.values_list("lab_order__patient_id", flat=True).distinct())

    disp_qs = _apply_date_filter(evt.dispenses.all(), "dispensed_at", filters)
    if filters.get("site_id"): disp_qs = _apply_patient_site_filter(disp_qs, filters)
    seen_ids |= ids_from(disp_qs)

    imm_qs = _apply_date_filter(evt.immunizations.all(), "administered_at", filters)
    if filters.get("site_id"): imm_qs = _apply_patient_site_filter(imm_qs, filters)
    seen_ids |= ids_from(imm_qs)

    blood_qs = _apply_date_filter(evt.blood_donations.all(), "recorded_at", filters)
    if filters.get("site_id"):
        blood_qs = blood_qs.filter(patient__site_id=filters.get("site_id"))
    seen_ids |= ids_from(blood_qs)

    coun_qs = _apply_date_filter(evt.counseling_sessions.all(), "recorded_at", filters)
    if filters.get("site_id"): coun_qs = _apply_patient_site_filter(coun_qs, filters)
    seen_ids |= ids_from(coun_qs)

    mat_qs = _apply_date_filter(evt.maternal_records.all(), "recorded_at", filters)
    if filters.get("site_id"): mat_qs = _apply_patient_site_filter(mat_qs, filters)
    seen_ids |= ids_from(mat_qs)

    ref_qs = _apply_date_filter(evt.referrals.all(), "recorded_at", filters)
    if filters.get("site_id"): ref_qs = _apply_patient_site_filter(ref_qs, filters)
    seen_ids |= ids_from(ref_qs)

    surg_qs = _apply_date_filter(evt.surgicals.all(), "recorded_at", filters)
    if filters.get("site_id"): surg_qs = _apply_patient_site_filter(surg_qs, filters)
    seen_ids |= ids_from(surg_qs)

    eye_qs = _apply_date_filter(evt.eye_checks.all(), "recorded_at", filters)
    if filters.get("site_id"): eye_qs = _apply_patient_site_filter(eye_qs, filters)
    seen_ids |= ids_from(eye_qs)

    dental_qs = _apply_date_filter(evt.dental_checks.all(), "recorded_at", filters)
    if filters.get("site_id"): dental_qs = _apply_patient_site_filter(dental_qs, filters)
    seen_ids |= ids_from(dental_qs)

    patients_seen = len(seen_ids)

    # Demographics (on registered patients in filtered scope)
    sex_counts = list(p_qs.values("sex").annotate(count=Count("id")).order_by("-count"))

    age_known = p_qs.exclude(age_years__isnull=True).count()
    age_total = patients_registered
    ages = list(p_qs.exclude(age_years__isnull=True).values_list("age_years", flat=True))
    youngest = min(ages) if ages else None
    oldest = max(ages) if ages else None

    bands = Counter()
    for a in ages:
        b = _age_band(a)
        if b:
            bands[b] += 1
    # stable order
    band_order = ["0-4","5-9","10-14","15-19","20-24","25-34","35-44","45-54","55-64","65+"]
    age_bands = [{"band": b, "count": int(bands.get(b, 0))} for b in band_order if bands.get(b, 0)]

    # Module usage
    module_rows = []
    def add_module(key, label, qs, patient_field="patient_id"):
        module_rows.append({
            "key": key,
            "label": label,
            "records": int(qs.count()),
            "patients": int(qs.values_list(patient_field, flat=True).distinct().count()) if patient_field else None,
        })

    add_module("vitals", "Vitals", vitals_qs)
    add_module("encounters", "Encounters", enc_qs)
    add_module("lab_orders", "Lab Orders", lab_orders_qs)
    # lab results anchored on order patient
    module_rows.append({
        "key": "lab_results",
        "label": "Lab Results",
        "records": int(lab_results_qs.count()),
        "patients": int(lab_results_qs.values_list("lab_order__patient_id", flat=True).distinct().count()),
    })
    add_module("dispenses", "Pharmacy Dispenses", disp_qs)
    add_module("immunizations", "Immunizations", imm_qs)
    add_module("blood_donations", "Blood Donations", blood_qs, patient_field="patient_id")
    add_module("counseling", "Counseling", coun_qs)
    add_module("maternal", "Maternal", mat_qs)
    add_module("referrals", "Referrals", ref_qs)
    add_module("surgicals", "Surgicals", surg_qs)
    add_module("eye_checks", "Eye Checks", eye_qs)
    add_module("dental_checks", "Dental Checks", dental_qs)

    module_rows.sort(key=lambda x: (x.get("records", 0) or 0), reverse=True)

    # Top items
    top_lab = []
    try:
        items_qs = OutreachLabOrderItem.objects.filter(lab_order__outreach_event=evt)
        if start:
            items_qs = items_qs.filter(created_at__date__gte=start)
        if end:
            items_qs = items_qs.filter(created_at__date__lte=end)
        if filters.get("site_id"):
            items_qs = items_qs.filter(lab_order__patient__site_id=filters.get("site_id"))
        top_lab = list(
            items_qs.values("test_name").exclude(test_name="").annotate(count=Count("id")).order_by("-count")[:10]
        )
    except Exception:
        top_lab = []

    top_vaccines = list(
        imm_qs.values("vaccine_name").exclude(vaccine_name="").annotate(count=Count("id")).order_by("-count")[:10]
    )

    top_drugs = list(
        disp_qs.values("drug_name").exclude(drug_name="").annotate(count=Count("id")).order_by("-count")[:10]
    )

    # Blood group/genotype distributions
    blood_group = list(blood_qs.values("blood_group").annotate(count=Count("id")).order_by("-count"))
    genotype = list(blood_qs.values("genotype").annotate(count=Count("id")).order_by("-count"))
    blood_combo = list(blood_qs.values("blood_group","genotype").annotate(count=Count("id")).order_by("-count"))

    # Maternal pregnancy breakdown: latest per patient within scope
    preg_counts = []
    if mat_qs.exists():
        # latest maternal per patient (by recorded_at)
        latest = {}
        for r in mat_qs.order_by("patient_id", "-recorded_at").values("patient_id","pregnancy_status","recorded_at"):
            pid = r["patient_id"]
            if pid not in latest:
                latest[pid] = r["pregnancy_status"]
        c = Counter(latest.values())
        preg_counts = [{"pregnancy_status": k, "count": int(v)} for k, v in c.most_common()]

    return {
        "event": {"id": evt.id, "title": evt.title, "status": evt.status, "starts_at": evt.starts_at, "ends_at": evt.ends_at, "closed_at": evt.closed_at},
        "filters": {
            "site_id": filters.get("site_id"),
            "from": filters.get("from"),
            "to": filters.get("to"),
        },
        "kpis": {
            "sites": evt.sites.count(),
            "staff": evt.staff_profiles.count(),
            "patients_registered": patients_registered,
            "patients_seen": patients_seen,
            "vitals": int(vitals_qs.count()),
            "encounters": int(enc_qs.count()),
            "lab_orders": int(lab_orders_qs.count()),
            "lab_results": int(lab_results_qs.count()),
            "dispenses": int(disp_qs.count()),
            "immunizations": int(imm_qs.count()),
            "blood_donations": int(blood_qs.count()),
            "counseling_sessions": int(coun_qs.count()),
            "maternal_records": int(mat_qs.count()),
            "referrals": int(ref_qs.count()),
            "surgicals": int(surg_qs.count()),
            "eye_checks": int(eye_qs.count()),
            "dental_checks": int(dental_qs.count()),
        },
        "demographics": {
            "sex": sex_counts,
            "age": {
                "known": age_known,
                "total": age_total,
                "youngest": youngest,
                "oldest": oldest,
                "bands": age_bands,
            },
        },
        "modules": module_rows,
        "top_items": {
            "lab_tests": top_lab,
            "vaccines": top_vaccines,
            "drugs": top_drugs,
        },
        "blood": {
            "blood_group": blood_group,
            "genotype": genotype,
            "combo": blood_combo[:20],
        },
        "maternal": {
            "pregnancy_status": preg_counts,
        },
    }


def build_patient_journey_payload(evt: OutreachEvent, patient_id: int, filters: dict):
    filters = filters if isinstance(filters, dict) else {}
    p = evt.patients.filter(id=patient_id).select_related("site").first()
    if not p:
        return {"detail": "Patient not found for this outreach event."}

    # For journey we use the same date/site filters, but patient_id already scoped.
    vitals = _apply_date_filter(evt.vitals.filter(patient_id=patient_id), "recorded_at", filters).order_by("-recorded_at")
    encounters = _apply_date_filter(evt.encounters.filter(patient_id=patient_id), "recorded_at", filters).order_by("-recorded_at")
    lab_orders = _apply_date_filter(evt.lab_orders.filter(patient_id=patient_id), "ordered_at", filters).order_by("-ordered_at")
    lab_results = _apply_date_filter(evt.lab_results.filter(lab_order__patient_id=patient_id), "recorded_at", filters).order_by("-recorded_at")
    dispenses = _apply_date_filter(evt.dispenses.filter(patient_id=patient_id), "dispensed_at", filters).order_by("-dispensed_at")
    immunizations = _apply_date_filter(evt.immunizations.filter(patient_id=patient_id), "administered_at", filters).order_by("-administered_at")
    blood_donations = _apply_date_filter(evt.blood_donations.filter(patient_id=patient_id), "recorded_at", filters).order_by("-recorded_at")
    counseling = _apply_date_filter(evt.counseling_sessions.filter(patient_id=patient_id), "recorded_at", filters).order_by("-recorded_at")
    maternal = _apply_date_filter(evt.maternal_records.filter(patient_id=patient_id), "recorded_at", filters).order_by("-recorded_at")
    referrals = _apply_date_filter(evt.referrals.filter(patient_id=patient_id), "recorded_at", filters).order_by("-recorded_at")
    surgicals = _apply_date_filter(evt.surgicals.filter(patient_id=patient_id), "recorded_at", filters).order_by("-recorded_at")
    eye_checks = _apply_date_filter(evt.eye_checks.filter(patient_id=patient_id), "recorded_at", filters).order_by("-recorded_at")
    dental_checks = _apply_date_filter(evt.dental_checks.filter(patient_id=patient_id), "recorded_at", filters).order_by("-recorded_at")

    def as_rows(qs, when_field, title, detail_fields):
        rows = []
        for obj in qs[:250]:
            when = getattr(obj, when_field, None)
            meta = {}
            for f in detail_fields:
                meta[f] = getattr(obj, f, None)
            rows.append({"when": when, "title": title, "meta": meta})
        return rows

    timeline = []
    timeline += as_rows(vitals, "recorded_at", "Vitals", ["weight_kg","height_cm","bmi","bp_sys","bp_dia","temp_c","pulse","notes"])
    timeline += as_rows(encounters, "recorded_at", "Encounter", ["complaint","diagnosis_tags","plan","notes","referral_note"])
    for lo in lab_orders[:250]:
        timeline.append({"when": lo.ordered_at, "title": "Lab Order", "meta": {"tests": [i.test_name for i in lo.items.all()]}})
    timeline += as_rows(lab_results, "recorded_at", "Lab Result", ["test_name","result_value","unit","notes"])
    timeline += as_rows(dispenses, "dispensed_at", "Pharmacy Dispense", ["drug_name","strength","quantity","instruction"])
    timeline += as_rows(immunizations, "administered_at", "Immunization", ["vaccine_name","dose_number","route","notes"])
    timeline += as_rows(blood_donations, "recorded_at", "Blood Donation", ["blood_group","genotype","eligibility_status","outcome","notes"])
    timeline += as_rows(counseling, "recorded_at", "Counseling", ["topics","session_notes","visibility","duration_minutes"])
    timeline += as_rows(maternal, "recorded_at", "Maternal", ["pregnancy_status","gestational_age_weeks","risk_flags","notes"])
    # Referral etc fields may vary; safer to stringify
    for r in referrals[:250]:
        timeline.append({"when": r.recorded_at, "title": "Referral", "meta": {"summary": str(r)}})
    for s in surgicals[:250]:
        timeline.append({"when": s.recorded_at, "title": "Surgical", "meta": {"summary": str(s)}})
    for e in eye_checks[:250]:
        timeline.append({"when": e.recorded_at, "title": "Eye Check", "meta": {"summary": str(e)}})
    for d in dental_checks[:250]:
        timeline.append({"when": d.recorded_at, "title": "Dental Check", "meta": {"summary": str(d)}})

    timeline.sort(key=lambda x: x.get("when") or timezone.now(), reverse=True)

    return {
        "event": {"id": evt.id, "title": evt.title},
        "patient": {
            "id": p.id,
            "patient_code": p.patient_code,
            "full_name": p.full_name,
            "sex": p.sex,
            "age_years": p.age_years,
            "phone": p.phone,
            "email": p.email,
            "site": p.site.name if p.site else "",
        },
        "timeline": timeline,
        "filters": {"from": filters.get("from"), "to": filters.get("to"), "site_id": filters.get("site_id")},
    }


def build_staff_activity_payload(evt: OutreachEvent, staff_user_id: int, filters: dict):
    filters = filters if isinstance(filters, dict) else {}

    staff = OutreachStaffProfile.objects.filter(outreach_event=evt, user_id=staff_user_id).select_related("user").first()
    if not staff:
        return {"detail": "Staff profile not found for this outreach event."}

    # scope by sites on staff profile if any, unless super admin passed explicit site filter
    site_id = filters.get("site_id")
    site_scope_ids = list(staff.sites.values_list("id", flat=True))
    if site_id:
        site_scope_ids = [int(site_id)] if str(site_id).isdigit() else site_scope_ids

    def patient_ids(qs):
        if site_scope_ids:
            qs = qs.filter(patient__site_id__in=site_scope_ids)
        return set(qs.values_list("patient_id", flat=True).distinct())

    vitals = _apply_date_filter(evt.vitals.filter(recorded_by_id=staff_user_id), "recorded_at", filters)
    encounters = _apply_date_filter(evt.encounters.filter(recorded_by_id=staff_user_id), "recorded_at", filters)
    lab_orders = _apply_date_filter(evt.lab_orders.filter(ordered_by_id=staff_user_id), "ordered_at", filters)
    lab_results = _apply_date_filter(evt.lab_results.filter(recorded_by_id=staff_user_id), "recorded_at", filters)
    dispenses = _apply_date_filter(evt.dispenses.filter(dispensed_by_id=staff_user_id), "dispensed_at", filters)
    immunizations = _apply_date_filter(evt.immunizations.filter(administered_by_id=staff_user_id), "administered_at", filters)
    blood = _apply_date_filter(evt.blood_donations.filter(recorded_by_id=staff_user_id), "recorded_at", filters)
    counseling = _apply_date_filter(evt.counseling_sessions.filter(counselor_id=staff_user_id), "recorded_at", filters)
    maternal = _apply_date_filter(evt.maternal_records.filter(recorded_by_id=staff_user_id), "recorded_at", filters)
    referrals = _apply_date_filter(evt.referrals.filter(recorded_by_id=staff_user_id), "recorded_at", filters)
    surgicals = _apply_date_filter(evt.surgicals.filter(recorded_by_id=staff_user_id), "recorded_at", filters)
    eye = _apply_date_filter(evt.eye_checks.filter(recorded_by_id=staff_user_id), "recorded_at", filters)
    dental = _apply_date_filter(evt.dental_checks.filter(recorded_by_id=staff_user_id), "recorded_at", filters)

    ids = set()
    for qs in [vitals, encounters, lab_orders, dispenses, immunizations, blood, counseling, maternal, referrals, surgicals, eye, dental]:
        ids |= patient_ids(qs)

    patients = list(
        evt.patients.filter(id__in=ids).select_related("site").order_by("patient_code").values("id","patient_code","full_name","sex","age_years")
    )

    by_module = [
        {"key":"vitals","label":"Vitals","records": int(vitals.count())},
        {"key":"encounters","label":"Encounters","records": int(encounters.count())},
        {"key":"lab_orders","label":"Lab Orders","records": int(lab_orders.count())},
        {"key":"lab_results","label":"Lab Results","records": int(lab_results.count())},
        {"key":"dispenses","label":"Pharmacy Dispenses","records": int(dispenses.count())},
        {"key":"immunizations","label":"Immunizations","records": int(immunizations.count())},
        {"key":"blood_donations","label":"Blood Donations","records": int(blood.count())},
        {"key":"counseling","label":"Counseling","records": int(counseling.count())},
        {"key":"maternal","label":"Maternal","records": int(maternal.count())},
        {"key":"referrals","label":"Referrals","records": int(referrals.count())},
        {"key":"surgicals","label":"Surgicals","records": int(surgicals.count())},
        {"key":"eye_checks","label":"Eye Checks","records": int(eye.count())},
        {"key":"dental_checks","label":"Dental Checks","records": int(dental.count())},
    ]
    by_module.sort(key=lambda x: x["records"], reverse=True)

    u = staff.user
    staff_name = f"{getattr(u,'first_name','')} {getattr(u,'last_name','')}".strip() or getattr(u,'email', 'Staff')

    return {
        "event": {"id": evt.id, "title": evt.title},
        "staff": {"user_id": staff_user_id, "name": staff_name, "email": getattr(u,'email', None)},
        "filters": {"from": filters.get("from"), "to": filters.get("to"), "site_id": filters.get("site_id")},
        "counts": {"patients_served": len(ids)},
        "by_module": by_module,
        "patients": patients[:500],
    }

def build_report_payload(evt: OutreachEvent, export_type: str, filters: dict):
    export_type = (export_type or "summary").strip().lower()
    filters = filters if isinstance(filters, dict) else {}

    # Analytics-style reports
    if export_type in ("executive_summary", "insights"):
        return build_insights_payload(evt, filters)

    if export_type == "patient_journey":
        pid = filters.get("patient_id") or filters.get("patient") or filters.get("id")
        if not pid:
            return {"detail": "patient_id is required for patient_journey."}
        return build_patient_journey_payload(evt, int(pid), filters)

    if export_type in ("staff_patients", "staff_activity"):
        sid = filters.get("staff_user_id") or filters.get("staff_id") or filters.get("user_id")
        if not sid:
            return {"detail": "staff_user_id is required for staff_patients."}
        return build_staff_activity_payload(evt, int(sid), filters)

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
    """Generate a PDF using HTML+CSS templates (WeasyPrint).

    Falls back to the existing text-based renderer only if WeasyPrint is unavailable.
    """
    export_type = (export_type or "summary").strip().lower()

    filename = f"outreach_{evt.id}_{export_type}.pdf"

    template = None
    ctx = {
        "generated_at": timezone.now(),
        "event": {"id": evt.id, "title": evt.title, "status": evt.status, "starts_at": evt.starts_at, "ends_at": evt.ends_at},
        "payload": payload,
    }

    if export_type in ("executive_summary", "insights"):
        template = "outreach_reports/executive_summary.html"
        ctx = {**ctx, **(payload or {})}
        filename = f"outreach_{evt.id}_executive_summary.pdf"
    elif export_type == "patient_journey":
        template = "outreach_reports/patient_journey.html"
        ctx = {**ctx, **(payload or {})}
        filename = f"outreach_{evt.id}_patient_journey.pdf"
    elif export_type in ("staff_patients", "staff_activity"):
        template = "outreach_reports/staff_patients.html"
        ctx = {**ctx, **(payload or {})}
        filename = f"outreach_{evt.id}_staff_patients.pdf"
    else:
        # For list-like exports (encounters, results, etc.) use a clean table template.
        if isinstance(payload, list):
            headers = list(payload[0].keys()) if payload else []
            template = "outreach_reports/table_export.html"
            ctx = {**ctx, "headers": headers, "rows": payload, "export_type": export_type}
        else:
            template = "outreach_reports/executive_summary.html"
            # try to show something reasonable
            if isinstance(payload, dict):
                ctx = {**ctx, "kpis": payload.get("counts") or payload.get("kpis") or {}, "demographics": payload.get("demographics") or {}, "modules": payload.get("modules") or [], "top_items": payload.get("top_items") or {}}

    try:
        from weasyprint import HTML
        html = render_to_string(template, ctx)
        pdf_bytes = HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()
        return pdf_bytes, filename
    except Exception:
        # fallback to the legacy text-based generator
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
        except Exception:
            text = f"Outreach {evt.title} - {export_type}\n\n{payload}"
            return text.encode("utf-8"), f"outreach_{evt.id}_{export_type}.txt"

        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
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
            c.drawString(40, y, str(line)[:110])
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
