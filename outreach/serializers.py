from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .constants import ALL_OPTIONAL_MODULES, ALL_PERMISSIONS, ROLE_TEMPLATES, OUTREACH_ROLE_TO_ACCOUNT_ROLE
from .enums import OutreachStatus, CounselingVisibility, LabOrderStatus
from .models import (
    OutreachEvent,
    OutreachSite,
    OutreachStaffProfile,
    OutreachPatient,
    OutreachVitals,
    OutreachEncounter,
    OutreachLabTestCatalog,
    OutreachLabOrder,
    OutreachLabOrderItem,
    OutreachLabResult,
    OutreachDrugCatalog,
    OutreachDispense,
    OutreachVaccineCatalog,
    OutreachImmunization,
    OutreachBloodDonation,
    OutreachReferral,
    OutreachSurgical,
    OutreachEyeCheck,
    OutreachDentalCheck,
    OutreachCounseling,
    OutreachMaternal,
    OutreachAuditLog,
    OutreachExport,
)

User = get_user_model()

class OutreachSiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachSite
        fields = ["id", "name", "community", "address", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]

class OutreachEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachEvent
        fields = ["id", "title", "description", "starts_at", "ends_at", "status", "modules_enabled", "created_at", "updated_at", "closed_at"]
        read_only_fields = ["id", "created_at", "updated_at", "closed_at"]

    def validate_modules_enabled(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("modules_enabled must be an object/dict.")
        # only allow known module keys
        cleaned = {}
        for k, v in value.items():
            k = str(k).strip()
            if k not in ALL_OPTIONAL_MODULES:
                continue
            cleaned[k] = bool(v)
        return cleaned

class OutreachEventDetailSerializer(OutreachEventSerializer):
    sites = OutreachSiteSerializer(many=True, read_only=True)
    stats = serializers.SerializerMethodField()

    class Meta(OutreachEventSerializer.Meta):
        fields = OutreachEventSerializer.Meta.fields + ["sites", "stats"]

    def get_stats(self, obj: OutreachEvent):
        # Lightweight counts for dashboard + quick context
        return {
            "sites": obj.sites.count(),
            "patients": obj.patients.count(),
            "staff": obj.staff_profiles.count(),
            "vitals": obj.vitals.count(),
            "encounters": obj.encounters.count(),
            "lab_orders": obj.lab_orders.count(),
            "lab_results": obj.lab_results.count(),
            "dispenses": obj.dispenses.count(),
            "immunizations": obj.immunizations.count(),
            "blood_donations": obj.blood_donations.count(),
            "counseling_sessions": obj.counseling_sessions.count(),
            "maternal_records": obj.maternal_records.count(),
            "referrals": obj.referrals.count(),
            "surgicals": obj.surgicals.count(),
            "eye_checks": obj.eye_checks.count(),
            "dental_checks": obj.dental_checks.count(),
        }

class OutreachStaffUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "role", "is_active"]

class OutreachStaffProfileSerializer(serializers.ModelSerializer):
    user = OutreachStaffUserSerializer(read_only=True)
    sites = OutreachSiteSerializer(many=True, read_only=True)

    class Meta:
        model = OutreachStaffProfile
        fields = ["id", "user", "phone", "role_template", "permissions", "all_sites", "sites", "is_active", "disabled_at", "created_at"]

class OutreachColleagueSerializer(serializers.ModelSerializer):
    """Staff-safe colleague serializer (no permissions list)."""

    user = OutreachStaffUserSerializer(read_only=True)
    sites = OutreachSiteSerializer(many=True, read_only=True)

    class Meta:
        model = OutreachStaffProfile
        fields = ["id", "user", "phone", "role_template", "all_sites", "sites", "is_active", "disabled_at", "created_at"]



class OutreachStaffCreateSerializer(serializers.Serializer):
    email = serializers.CharField()
    phone = serializers.CharField(required=False, allow_blank=True, default="")
    full_name = serializers.CharField(required=False, allow_blank=True, default="")
    role_template = serializers.CharField(required=False, allow_blank=True, default="")
    permissions = serializers.ListField(child=serializers.CharField(), required=False)
    all_sites = serializers.BooleanField(required=False, default=True)
    site_ids = serializers.ListField(child=serializers.IntegerField(), required=False)

    def validate_email(self, value):
        v = (value or "").strip().lower()
        if not v:
            raise serializers.ValidationError("email is required")
        if "@" not in v:
            raise serializers.ValidationError("email must be a valid email address")
        return v

    def validate_permissions(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("permissions must be a list")
        perms = []
        for p in value:
            p = str(p).strip()
            if p in ALL_PERMISSIONS:
                perms.append(p)
        return sorted(set(perms))

    def validate(self, data):
        # If permissions not provided, use role template defaults
        perms = data.get("permissions", None)
        role_template = (data.get("role_template") or "").strip()
        if perms is None:
            data["permissions"] = sorted(list(ROLE_TEMPLATES.get(role_template, set())))
        elif isinstance(perms, list) and len(perms) == 0 and role_template in ROLE_TEMPLATES:
            # allow explicit empty, but better to set defaults if they selected a role but didn't pick permissions
            data["permissions"] = sorted(list(ROLE_TEMPLATES.get(role_template, set())))
        return data

    def get_account_role(self):
        role_template = (self.validated_data.get("role_template") or "").strip()
        return OUTREACH_ROLE_TO_ACCOUNT_ROLE.get(role_template, "ADMIN")


class OutreachStaffUpdateSerializer(serializers.Serializer):
    # All fields optional; used for PATCH update on a staff profile
    phone = serializers.CharField(required=False, allow_blank=True)
    role_template = serializers.CharField(required=False, allow_blank=True)
    permissions = serializers.ListField(child=serializers.CharField(), required=False)
    all_sites = serializers.BooleanField(required=False)
    site_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    is_active = serializers.BooleanField(required=False)

    def validate_permissions(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("permissions must be a list")
        perms = []
        for p in value:
            p = str(p).strip()
            if p in ALL_PERMISSIONS:
                perms.append(p)
        # Deduplicate while preserving order
        seen = set()
        out = []
        for p in perms:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

class OutreachPatientSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachPatient
        fields = [
            "id","outreach_event","site","patient_code","full_name","sex","date_of_birth","age_years",
            "phone","email","community","address","created_by","created_at","updated_at"
        ]
        read_only_fields = ["id","patient_code","created_by","created_at","updated_at","outreach_event"]

class OutreachVitalsSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachVitals
        fields = [
            "id","outreach_event","patient","bp_sys","bp_dia","pulse","temp_c","weight_kg","height_cm","bmi",
            "recorded_by","recorded_at","updated_at"
        ]
        read_only_fields = ["id","bmi","recorded_by","recorded_at","updated_at","outreach_event"]

class OutreachEncounterSerializer(serializers.ModelSerializer):
    
    def validate_diagnosis_tags(self, value):
        # Accept JSON strings from multipart/form-data
        if value in (None, ""):
            return []
        if isinstance(value, str):
            import json
            try:
                v = json.loads(value)
                if isinstance(v, list):
                    return v
            except Exception:
                # fall back to comma-separated
                return [x.strip() for x in value.split(",") if x.strip()]
        return value

    class Meta:
        model = OutreachEncounter
        fields = ["id","outreach_event","patient","complaint","notes","diagnosis_tags","plan","referral_note","recorded_by","recorded_at","updated_at","soap_note_attachment"]
        read_only_fields = ["id","recorded_by","recorded_at","updated_at","outreach_event"]

class OutreachLabTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachLabTestCatalog
        fields = ["id","outreach_event","code","name","unit","ref_low","ref_high","price","is_active","created_at"]
        read_only_fields = ["id","created_at"]

class OutreachLabOrderItemSerializer(serializers.ModelSerializer):
    test = OutreachLabTestSerializer(read_only=True)

    class Meta:
        model = OutreachLabOrderItem
        fields = ["id","test","test_name","created_at"]
        read_only_fields = ["id","created_at"]

class OutreachLabOrderSerializer(serializers.ModelSerializer):
    items = OutreachLabOrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = OutreachLabOrder
        fields = [
            "id","outreach_event","patient","status","notes","ordered_by","ordered_at",
            "collected_at","result_ready_at","updated_at","items"
        ]
        read_only_fields = ["id","ordered_by","ordered_at","updated_at","items"]

class OutreachLabOrderCreateSerializer(serializers.Serializer):
    patient_id = serializers.IntegerField()
    test_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

class OutreachLabResultSerializer(serializers.ModelSerializer):
    # Supports structured multi-figure results (list of rows) in addition to simple result_value.
    result_data = serializers.JSONField(required=False, allow_null=True)

    def validate(self, attrs):
        # Multipart form-data sends JSON fields as strings; parse if needed.
        rd = attrs.get("result_data", None)
        if isinstance(rd, str):
            if rd.strip() == "":
                attrs["result_data"] = None
            else:
                try:
                    attrs["result_data"] = json.loads(rd)
                except Exception:
                    raise serializers.ValidationError({"result_data": "Invalid JSON."})

        result_value = (attrs.get("result_value") or "").strip()
        attachment = attrs.get("result_attachment")
        result_data = attrs.get("result_data")

        # Normalize list rows: keep only meaningful dicts
        if isinstance(result_data, list):
            cleaned = []
            for row in result_data:
                if not isinstance(row, dict):
                    continue
                # accept either 'ref_range' or 'reference_range'
                rr = row.get("ref_range", row.get("reference_range", ""))
                if rr is not None and "ref_range" not in row:
                    row["ref_range"] = rr
                if any(str(row.get(k, "")).strip() for k in ("name", "value", "unit", "ref_range")):
                    cleaned.append({
                        "name": str(row.get("name", "")).strip(),
                        "value": str(row.get("value", "")).strip(),
                        "unit": str(row.get("unit", "")).strip(),
                        "ref_range": str(row.get("ref_range", "")).strip(),
                    })
            attrs["result_data"] = cleaned
            result_data = cleaned

        has_structured = bool(result_data)
        if not result_value and not attachment and not has_structured:
            raise serializers.ValidationError({"result_value": "Provide result value, result rows, or upload an attachment."})

        return attrs

    class Meta:
        model = OutreachLabResult
        fields = [
            "id",
            "outreach_event",
            "lab_order",
            "item",
            "test_name",
            "result_value",
            "unit",
            "notes",
            "result_attachment",
            "result_data",
            "recorded_by",
            "recorded_at",
            "updated_at",
        ]
        read_only_fields = ["id", "recorded_by", "recorded_at", "updated_at", "outreach_event"]

class OutreachDrugSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachDrugCatalog
        fields = [
            "id","outreach_event","code","name","strength","form","route","qty_per_unit","unit_price","is_active","created_at"
        ]
        read_only_fields = ["id","created_at"]

class OutreachDispenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachDispense
        fields = [
            "id","outreach_event","patient","drug","drug_name","strength","quantity","instruction",
            "dispensed_by","dispensed_at","updated_at"
        ]
        read_only_fields = ["id","dispensed_by","dispensed_at","updated_at","drug_name","strength"]

class OutreachDispenseCreateSerializer(serializers.Serializer):
    patient_id = serializers.IntegerField()
    drug_id = serializers.IntegerField(required=False)
    drug_name = serializers.CharField(required=False, allow_blank=True, default="")
    strength = serializers.CharField(required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2)
    instruction = serializers.CharField(required=False, allow_blank=True, default="")

class OutreachVaccineCatalogSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachVaccineCatalog
        fields = ["id","outreach_event","code","name","manufacturer","notes","is_active","created_by","created_at"]
        read_only_fields = ["id","outreach_event","created_by","created_at"]

class OutreachImmunizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachImmunization
        fields = [
            "id","outreach_event","patient","vaccine_name","dose_number","batch_number","route","notes",
            "administered_by","administered_at"
        ]
        read_only_fields = ["id","outreach_event","administered_by","administered_at"]

class OutreachBloodDonationSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachBloodDonation
        fields = [
            "id","outreach_event","patient","blood_group","genotype","eligibility_status","deferral_reason","outcome","notes",
            "recorded_by","recorded_at"
        ]
        read_only_fields = ["id","outreach_event","recorded_by","recorded_at"]


class OutreachReferralSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachReferral
        fields = [
            "id","outreach_event","patient","referred_to","referral_type","reason_for_referral","recorded_by","recorded_at","updated_at"
        ]
        read_only_fields = ["id","outreach_event","recorded_by","recorded_at","updated_at"]


class OutreachSurgicalSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachSurgical
        fields = [
            "id","outreach_event","patient","procedure_category","procedure_name","indication","consent_obtained","status","recorded_by","recorded_at","updated_at"
        ]
        read_only_fields = ["id","outreach_event","recorded_by","recorded_at","updated_at"]


class OutreachEyeCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachEyeCheck
        fields = [
            "id","outreach_event","patient","visit_type","chief_complaint","visual_acuity_right","visual_acuity_left","eye_exam_findings","assessment_diagnosis","plan","status","recorded_by","recorded_at","updated_at"
        ]
        read_only_fields = ["id","outreach_event","recorded_by","recorded_at","updated_at"]


class OutreachDentalCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachDentalCheck
        fields = [
            "id","outreach_event","patient","visit_type","chief_complaint","oral_examination_findings","diagnosis_assessment","procedure_done","tooth_area_involved","plan","status","recorded_by","recorded_at","updated_at"
        ]
        read_only_fields = ["id","outreach_event","recorded_by","recorded_at","updated_at"]

class OutreachCounselingSerializer(serializers.ModelSerializer):
    # Accept legacy frontend values: NORMAL/SENSITIVE/RESTRICTED
    visibility_level = serializers.CharField(required=False, allow_blank=True)
    topics = serializers.JSONField(required=False)

    def validate_visibility_level(self, value):
        v = (value or "").strip().upper()
        if not v:
            return CounselingVisibility.PRIVATE
        if v == "NORMAL":
            return CounselingVisibility.INTERNAL
        if v in ("SENSITIVE", "RESTRICTED"):
            return CounselingVisibility.PRIVATE
        if v in (CounselingVisibility.PRIVATE, CounselingVisibility.INTERNAL):
            return v
        raise serializers.ValidationError("Invalid visibility level.")

    def validate_topics(self, value):
        # Allow a comma-separated string from the UI
        if value is None:
            return []
        if isinstance(value, str):
            items = [x.strip() for x in value.split(",") if x.strip()]
            return items or ([value.strip()] if value.strip() else [])
        return value

    class Meta:
        model = OutreachCounseling
        fields = [
            "id","outreach_event","patient","topics","session_notes","duration_minutes","counselor","recorded_at","visibility_level"
        ]
        read_only_fields = ["id","outreach_event","counselor","recorded_at"]

class OutreachMaternalSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachMaternal
        fields = [
            "id","outreach_event","patient","pregnancy_status","gestational_age_weeks","risk_flags","notes",
            "recorded_by","recorded_at"
        ]
        read_only_fields = ["id","outreach_event","recorded_by","recorded_at"]

class OutreachAuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.SerializerMethodField()

    class Meta:
        model = OutreachAuditLog
        fields = ["id","outreach_event","actor_email","action","meta","created_at"]
        read_only_fields = fields

    def get_actor_email(self, obj):
        return getattr(obj.actor, "email", None)

class OutreachExportSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachExport
        fields = ["id","outreach_event","export_type","export_format","filters","file","created_at"]
        read_only_fields = ["id","file","created_at"]
