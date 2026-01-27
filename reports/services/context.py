from __future__ import annotations

"""Report context builders.

Enhanced to provide facility/provider information for report headers.
"""

from decimal import Decimal

from django.apps import apps
from django.conf import settings
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone


def _model(app_label: str, model_name: str):
    # Lazy model resolver to avoid import-time errors
    return apps.get_model(app_label, model_name)


def brand():
    """Legacy brand info - now used for 'powered by' footer only"""
    return getattr(settings, "REPORTS_BRAND", {
        "name": "NIEMR",
        "tagline": "Healthcare Management System"
    })


def _clean_text(v) -> str:
    return (v or "").strip()


def _split_bullets(text: str):
    """Split a free-text field into bullet-like items.

    - Prefer newline separation.
    - If single-line, fall back to semicolon / comma separation.
    """

    t = _clean_text(text)
    if not t:
        return []

    t = t.replace("\r", "\n")
    lines = [ln.strip().lstrip("-•*\t ") for ln in t.split("\n")]
    lines = [ln for ln in lines if ln]

    if len(lines) == 1:
        single = lines[0]
        sep = ";" if ";" in single else ("," if "," in single else None)
        if sep:
            parts = [p.strip() for p in single.split(sep) if p.strip()]
            if len(parts) > 1:
                return parts

    return lines


def _get_header_info(facility=None, provider=None):
    """Build header information for reports.
    
    Returns dict with:
    - entity_name: Name of facility or provider
    - entity_type: "Facility" or "Provider"
    - address: Full address if available
    - phone: Contact phone
    - email: Contact email
    - registration_number: Facility registration or provider license
    """
    if facility:
        return {
            "entity_name": facility.name,
            "entity_type": "Facility",
            "address": _clean_text(getattr(facility, "address", "")),
            "city": _clean_text(getattr(facility, "city", "")),
            "state": facility.get_state_display() if hasattr(facility, "get_state_display") else getattr(facility, "state", ""),
            "phone": _clean_text(getattr(facility, "phone", "")),
            "email": _clean_text(getattr(facility, "email", "")),
            "registration_number": _clean_text(getattr(facility, "registration_number", "")),
        }
    elif provider:
        # Provider from ProviderProfile
        user = getattr(provider, "user", None)
        name = ""
        email = ""
        if user:
            name = user.get_full_name() if hasattr(user, "get_full_name") else f"{user.first_name} {user.last_name}".strip()
            email = user.email
        
        return {
            "entity_name": name or "Independent Provider",
            "entity_type": "Provider",
            "provider_type": provider.get_provider_type_display() if hasattr(provider, "get_provider_type_display") else "",
            "address": _clean_text(getattr(provider, "address", "")),
            "city": "",
            "state": _clean_text(getattr(provider, "state", "")),
            "phone": _clean_text(getattr(provider, "phone", "")),
            "email": email,
            "registration_number": f"{getattr(provider, 'license_council', '')} {getattr(provider, 'license_number', '')}".strip(),
        }
    
    return {
        "entity_name": "Healthcare Provider",
        "entity_type": "Facility",
        "address": "",
        "city": "",
        "state": "",
        "phone": "",
        "email": "",
        "registration_number": "",
    }


def _format_ref_range(lo, hi) -> str:
    if lo is None and hi is None:
        return ""
    if lo is None:
        return f"≤ {hi}"
    if hi is None:
        return f"≥ {lo}"
    return f"{lo} – {hi}"


def encounter_context(encounter_id: int) -> dict:
    """Template context for reports/templates/reports/encounter.html."""

    Encounter = _model("encounters", "Encounter")
    LabOrder = _model("labs", "LabOrder")
    LabOrderItem = _model("labs", "LabOrderItem")
    Prescription = _model("pharmacy", "Prescription")
    PrescriptionItem = _model("pharmacy", "PrescriptionItem")

    enc = (
        Encounter.objects.select_related(
            "patient",
            "facility",
            "created_by",
            "nurse",
            "provider",
            "paused_by",
            "resumed_by",
            "labs_skipped_by",
            "clinical_finalized_by",
        ).get(id=encounter_id)
    )

    diagnoses_text = _clean_text(getattr(enc, "diagnoses", ""))
    plan_text = _clean_text(getattr(enc, "plan", ""))

    # The PDF template renders these lists as bullets.
    diagnoses_list = _split_bullets(diagnoses_text)
    plan_list = _split_bullets(plan_text)

    # Determine if this is facility-based or independent provider
    header_info = _get_header_info(
        facility=enc.facility,
        provider=None  # Could enhance to check if provider is independent
    )

    # Calculate encounter duration
    duration_display = None
    if enc.occurred_at or enc.created_at:
        from datetime import datetime
        start_time = enc.occurred_at or enc.created_at
        end_time = enc.clinical_finalized_at or timezone.now()
        
        if isinstance(start_time, str):
            start_time = timezone.datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        if isinstance(end_time, str):
            end_time = timezone.datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            
        total_minutes = int((end_time - start_time).total_seconds() / 60)
        
        # Subtract paused time if applicable
        if enc.paused_at and enc.resumed_at:
            paused_minutes = int((enc.resumed_at - enc.paused_at).total_seconds() / 60)
            total_minutes -= paused_minutes
        
        if total_minutes < 0:
            total_minutes = 0
            
        if total_minutes < 60:
            duration_display = f"{total_minutes} minutes"
        elif total_minutes < 1440:  # Less than 24 hours
            hours = total_minutes // 60
            mins = total_minutes % 60
            duration_display = f"{hours}h {mins}m" if mins > 0 else f"{hours}h"
        else:
            days = total_minutes // 1440
            hours = (total_minutes % 1440) // 60
            duration_display = f"{days}d {hours}h" if hours > 0 else f"{days}d"

    # Fetch lab orders for this encounter
    lab_orders = (
        LabOrder.objects.filter(encounter_id=encounter_id)
        .select_related("ordered_by")
        .prefetch_related("items__test")
        .order_by("-ordered_at")
    )
    
    lab_orders_data = []
    for order in lab_orders:
        items_data = []
        for item in order.items.all():
            test_name = getattr(item, "display_name", None) or getattr(item, "requested_name", "")
            test_name = (test_name or "").strip() or "—"
            
            val = _clean_text(getattr(item, "result_text", ""))
            if not val and getattr(item, "result_value", None) is not None:
                val = str(item.result_value)
            value = val or "—"
            
            unit = _clean_text(getattr(item, "result_unit", ""))
            if not unit and getattr(item, "test_id", None):
                unit = _clean_text(getattr(item.test, "unit", ""))
            
            lo = getattr(item, "ref_low", None)
            hi = getattr(item, "ref_high", None)
            ref_range = _format_ref_range(lo, hi)
            
            items_data.append({
                "test_name": test_name,
                "value": value,
                "unit": unit,
                "ref_range": ref_range,
                "flag": _clean_text(getattr(item, "flag", "")),
                "status": getattr(item, "status", ""),
            })
        
        ordered_by_name = None
        if order.ordered_by:
            ordered_by_name = order.ordered_by.get_full_name() if hasattr(order.ordered_by, "get_full_name") else order.ordered_by.email
        
        lab_orders_data.append({
            "id": order.id,
            "status": getattr(order, "status", ""),
            "priority": getattr(order, "priority", ""),
            "ordered_by": ordered_by_name,
            "ordered_at": order.ordered_at,
            "items": items_data,
            "note": _clean_text(getattr(order, "note", "")),
        })

    # Fetch prescriptions for this encounter
    prescriptions = (
        Prescription.objects.filter(encounter_id=encounter_id)
        .select_related("prescribed_by", "patient")
        .prefetch_related("items__drug")
        .order_by("-id")  # Use ID ordering as fallback
    )
    
    prescriptions_data = []
    for rx in prescriptions:
        items_data = []
        for item in rx.items.all():
            drug_name = getattr(item, "drug_name", None)
            if not drug_name and item.drug:
                drug_name = item.drug.name
            
            items_data.append({
                "drug_name": drug_name or "—",
                "dose": _clean_text(getattr(item, "dose", "")),
                "frequency": _clean_text(getattr(item, "frequency", "")),
                "duration_days": getattr(item, "duration_days", None),
                "instruction": _clean_text(getattr(item, "instruction", "")),
            })
        
        prescriber_name = None
        if rx.prescribed_by:
            prescriber_name = rx.prescribed_by.get_full_name() if hasattr(rx.prescribed_by, "get_full_name") else rx.prescribed_by.email
        
        # Get timestamp - check for prescribed_at or created_at
        rx_timestamp = getattr(rx, "prescribed_at", None) or getattr(rx, "created_at", None)
        
        prescriptions_data.append({
            "id": rx.id,
            "status": getattr(rx, "status", ""),
            "prescriber": prescriber_name,
            "timestamp": rx_timestamp,
            "items": items_data,
            "note": _clean_text(getattr(rx, "note", "")),
        })

    # Build timeline events
    timeline_events = []
    
    if enc.occurred_at or enc.created_at:
        timeline_events.append({
            "title": "Encounter Started",
            "timestamp": enc.occurred_at or enc.created_at,
            "actor": enc.created_by.get_full_name() if enc.created_by and hasattr(enc.created_by, "get_full_name") else None,
        })
    
    if enc.paused_at:
        actor = enc.paused_by.get_full_name() if enc.paused_by and hasattr(enc.paused_by, "get_full_name") else None
        timeline_events.append({
            "title": "Paused (Waiting for Labs)",
            "timestamp": enc.paused_at,
            "actor": actor,
        })
    
    if enc.resumed_at:
        actor = enc.resumed_by.get_full_name() if enc.resumed_by and hasattr(enc.resumed_by, "get_full_name") else None
        timeline_events.append({
            "title": "Resumed",
            "timestamp": enc.resumed_at,
            "actor": actor,
        })
    
    if enc.labs_skipped_at:
        actor = enc.labs_skipped_by.get_full_name() if enc.labs_skipped_by and hasattr(enc.labs_skipped_by, "get_full_name") else None
        timeline_events.append({
            "title": "Labs Skipped",
            "timestamp": enc.labs_skipped_at,
            "actor": actor,
        })
    
    if enc.clinical_finalized_at:
        actor = enc.clinical_finalized_by.get_full_name() if enc.clinical_finalized_by and hasattr(enc.clinical_finalized_by, "get_full_name") else None
        timeline_events.append({
            "title": "Clinical Documentation Finalized",
            "timestamp": enc.clinical_finalized_at,
            "actor": actor,
        })
    
    if enc.locked_at:
        timeline_events.append({
            "title": "Note Locked",
            "timestamp": enc.locked_at,
            "actor": None,
        })

    return {
        "brand": brand(),
        "header": header_info,
        "generated_at": timezone.now(),
        "title": f"Encounter #{enc.id}",
        "encounter": enc,
        "patient": enc.patient,
        "facility": enc.facility,
        # Best "author" for an encounter is the provider if set; else created_by.
        "author": enc.provider or enc.created_by,
        "created_by": enc.created_by,
        "nurse": getattr(enc, "nurse", None),
        "provider": getattr(enc, "provider", None),
        # Duration
        "duration_display": duration_display,
        # Clinical sections
        "chief_complaint": _clean_text(getattr(enc, "chief_complaint", "")),
        "hpi": _clean_text(getattr(enc, "hpi", "")),
        "ros": _clean_text(getattr(enc, "ros", "")),
        "physical_exam": _clean_text(getattr(enc, "physical_exam", "")),
        # Assessment/Plan
        "diagnoses_text": diagnoses_text,
        "diagnoses_list": diagnoses_list,
        "plan_text": plan_text,
        "plan_list": plan_list,
        # Lab orders and prescriptions
        "lab_orders": lab_orders_data,
        "prescriptions": prescriptions_data,
        # Timeline
        "timeline_events": timeline_events,
    }


def lab_context(order_id: int) -> dict:
    """Template context for reports/templates/reports/lab.html."""

    LabOrder = _model("labs", "LabOrder")

    order = (
        LabOrder.objects.select_related("patient", "facility", "ordered_by")
        .prefetch_related("items__test")
        .get(id=order_id)
    )

    # Build template-friendly results. The template expects:
    #   r.test_name, r.value, r.unit, r.ref_range, r.flag
    results = []
    for item in order.items.all():
        test_name = getattr(item, "display_name", None) or getattr(item, "requested_name", "")
        test_name = (test_name or "").strip() or "—"

        # result value could be numeric or text
        val = _clean_text(getattr(item, "result_text", ""))
        if not val and getattr(item, "result_value", None) is not None:
            val = str(item.result_value)
        value = val or "—"

        unit = _clean_text(getattr(item, "result_unit", ""))
        if not unit and getattr(item, "test_id", None):
            unit = _clean_text(getattr(item.test, "unit", ""))

        lo = getattr(item, "ref_low", None)
        hi = getattr(item, "ref_high", None)
        ref_range = _format_ref_range(lo, hi)

        results.append(
            {
                "test_name": test_name,
                "value": value,
                "unit": unit,
                "ref_range": ref_range,
                "flag": _clean_text(getattr(item, "flag", "")),
            }
        )

    header_info = _get_header_info(facility=order.facility)

    return {
        "brand": brand(),
        "header": header_info,
        "generated_at": timezone.now(),
        "title": f"Lab Order #{order.id}",
        "order": order,
        "patient": order.patient,
        "facility": order.facility,
        "ordered_by": getattr(order, "ordered_by", None),
        "results": results,
        "status": getattr(order, "status", ""),
        # this model uses `note`, template expects `indication`
        "indication": _clean_text(getattr(order, "note", "")),
    }


def imaging_context(request_id: int) -> dict:
    ImagingRequest = _model("imaging", "ImagingRequest")
    req = (
        ImagingRequest.objects.select_related(
            "patient", "facility", "requested_by", "procedure", "report"
        ).get(id=request_id)
    )
    report = getattr(req, "report", None)
    assets = getattr(report, "assets", None)
    images = list(assets.all()) if hasattr(assets, "all") else []

    header_info = _get_header_info(facility=req.facility)

    return {
        "brand": brand(),
        "header": header_info,
        "generated_at": timezone.now(),
        "title": f"Imaging Request #{req.id}",
        "request": req,
        "patient": req.patient,
        "facility": req.facility,
        "procedure": getattr(req, "procedure", None),
        "report": report,
        "images": images,
    }


def billing_context(
    patient_id: int,
    *,
    start=None,
    end=None,
    charge_id: int | None = None,
) -> dict:
    """Patient billing statement.

    Upgraded to use PaymentAllocation (collected vs billed) like the billing module.
    Also includes allocations from bulk/HMO payments that are applied to this patient's charges.
    """

    Charge = _model("billing", "Charge")
    Payment = _model("billing", "Payment")
    PaymentAllocation = _model("billing", "PaymentAllocation")
    Patient = _model("patients", "Patient")

    z = Value(Decimal("0.00"), output_field=DecimalField(max_digits=12, decimal_places=2))

    charge_scope = (
        Charge.objects.select_related("service", "facility", "patient")
        .filter(patient_id=patient_id)
        .exclude(status="VOID")
    )

    if charge_id:
        charge_scope = charge_scope.filter(id=charge_id)

    if start:
        charge_scope = charge_scope.filter(created_at__gte=start)
    if end:
        charge_scope = charge_scope.filter(created_at__lte=end)

    # Charges with allocation totals
    charges = (
        charge_scope.annotate(
            paid_amount=Coalesce(Sum("allocations__amount"), z),
        )
        .annotate(
            outstanding=ExpressionWrapper(
                F("amount") - F("paid_amount"),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
        .order_by("created_at", "id")
    )

    charges_total = charge_scope.aggregate(s=Coalesce(Sum("amount"), Decimal("0.00")))["s"]
    allocated_total = (
        PaymentAllocation.objects.filter(charge__in=charge_scope)
        .aggregate(s=Coalesce(Sum("amount"), Decimal("0.00")))["s"]
    )
    outstanding_total = Decimal(charges_total) - Decimal(allocated_total)

    # Payments relevant to this statement
    payments = (
        Payment.objects.select_related(
            "facility",
            "patient",
            "hmo",
            "system_hmo",
            "facility_hmo",
        )
        .filter(Q(patient_id=patient_id) | Q(allocations__charge__in=charge_scope))
        .distinct()
    )

    # For the payment table:
    # - allocated_to_statement: how much of this payment is applied to charges in this statement
    # - allocated_total: total allocated overall (used to compute payment unallocated)
    payments = (
        payments.annotate(
            allocated_to_statement=Coalesce(
                Sum("allocations__amount", filter=Q(allocations__charge__in=charge_scope)),
                z,
            ),
            allocated_total=Coalesce(Sum("allocations__amount"), z),
        )
        .annotate(
            unallocated=ExpressionWrapper(
                F("amount") - F("allocated_total"),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
        .order_by("received_at", "id")
    )

    patient = Patient.objects.select_related("facility", "system_hmo", "hmo_tier").get(
        id=patient_id
    )

    header_info = _get_header_info(facility=getattr(patient, "facility", None))

    return {
        "brand": brand(),
        "header": header_info,
        "generated_at": timezone.now(),
        "title": f"Billing Statement — Patient #{patient.id}",
        "patient": patient,
        "facility": getattr(patient, "facility", None),
        "charges": charges,
        "payments": payments,
        "charges_total": charges_total,
        "allocated_total": allocated_total,
        "outstanding_total": outstanding_total,
        "period": {"start": start, "end": end},
        "is_receipt": bool(charge_id),
    }


def hmo_statement_context(
    facility_hmo_id: int,
    *,
    start=None,
    end=None,
) -> dict:
    """Facility/provider HMO statement (FacilityHMO anchored).

    Uses FacilityHMO.id (junction table) to build a statement that:
    - lists charges for patients under the HMO
    - lists HMO payments and how they were allocated
    - shows outstanding amounts
    """

    FacilityHMO = _model("patients", "FacilityHMO")
    Charge = _model("billing", "Charge")
    Payment = _model("billing", "Payment")
    PaymentAllocation = _model("billing", "PaymentAllocation")
    ProviderProfile = _model("providers", "ProviderProfile")

    z = Value(Decimal("0.00"), output_field=DecimalField(max_digits=12, decimal_places=2))

    fhmo = FacilityHMO.objects.select_related("facility", "owner", "system_hmo").get(
        id=facility_hmo_id
    )

    provider_profile = None
    if fhmo.owner_id:
        provider_profile = ProviderProfile.objects.filter(user_id=fhmo.owner_id).first()

    header_info = _get_header_info(
        facility=getattr(fhmo, "facility", None),
        provider=provider_profile,
    )

    # Charges in scope
    charge_scope = (
        Charge.objects.select_related("patient", "service")
        .filter(patient__system_hmo_id=fhmo.system_hmo_id)
        .exclude(status="VOID")
    )

    if fhmo.facility_id:
        charge_scope = charge_scope.filter(facility_id=fhmo.facility_id)
    elif fhmo.owner_id:
        charge_scope = charge_scope.filter(owner_id=fhmo.owner_id)

    if start:
        charge_scope = charge_scope.filter(created_at__gte=start)
    if end:
        charge_scope = charge_scope.filter(created_at__lte=end)

    hmo_alloc_filter = Q(allocations__payment__payment_source="HMO") & (
        Q(allocations__payment__facility_hmo_id=fhmo.id)
        | Q(allocations__payment__system_hmo_id=fhmo.system_hmo_id)
    )

    charges = (
        charge_scope.annotate(
            paid_total=Coalesce(Sum("allocations__amount"), z),
            paid_hmo=Coalesce(Sum("allocations__amount", filter=hmo_alloc_filter), z),
        )
        .annotate(
            paid_other=ExpressionWrapper(
                F("paid_total") - F("paid_hmo"),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            outstanding=ExpressionWrapper(
                F("amount") - F("paid_total"),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        )
        .order_by("created_at", "id")
    )

    charges_total = charge_scope.aggregate(s=Coalesce(Sum("amount"), Decimal("0.00")))["s"]
    allocated_total = (
        PaymentAllocation.objects.filter(charge__in=charge_scope)
        .aggregate(s=Coalesce(Sum("amount"), Decimal("0.00")))["s"]
    )
    outstanding_total = Decimal(charges_total) - Decimal(allocated_total)

    hmo_allocated_total = (
        PaymentAllocation.objects.filter(charge__in=charge_scope)
        .filter(payment__payment_source="HMO")
        .filter(
            Q(payment__facility_hmo_id=fhmo.id)
            | Q(payment__system_hmo_id=fhmo.system_hmo_id)
        )
        .aggregate(s=Coalesce(Sum("amount"), Decimal("0.00")))["s"]
    )
    other_allocated_total = Decimal(allocated_total) - Decimal(hmo_allocated_total)

    # HMO payments for this FacilityHMO
    payments = Payment.objects.select_related(
        "facility",
        "facility_hmo",
        "system_hmo",
        "hmo",
        "received_by",
    ).filter(
        Q(facility_hmo_id=fhmo.id)
        | (
            Q(system_hmo_id=fhmo.system_hmo_id)
            & Q(facility_hmo__isnull=True)
        )
    )

    if fhmo.facility_id:
        payments = payments.filter(facility_id=fhmo.facility_id)
    elif fhmo.owner_id:
        payments = payments.filter(owner_id=fhmo.owner_id)

    if start:
        payments = payments.filter(received_at__gte=start)
    if end:
        payments = payments.filter(received_at__lte=end)

    payments = (
        payments.annotate(
            allocated_to_statement=Coalesce(
                Sum("allocations__amount", filter=Q(allocations__charge__in=charge_scope)),
                z,
            ),
            allocated_total=Coalesce(Sum("allocations__amount"), z),
        )
        .annotate(
            unallocated=ExpressionWrapper(
                F("amount") - F("allocated_total"),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
        .order_by("received_at", "id")
    )

    # Patient summary for this statement
    patient_rows = list(
        charge_scope.values(
            "patient_id",
            "patient__first_name",
            "patient__last_name",
        )
        .annotate(
            charges_total=Coalesce(Sum("amount"), Decimal("0.00")),
            charge_count=Count("id"),
        )
        .order_by("patient__last_name", "patient__first_name")
    )

    collected_by_patient = (
        PaymentAllocation.objects.filter(charge__in=charge_scope)
        .values("charge__patient_id")
        .annotate(collected_total=Coalesce(Sum("amount"), Decimal("0.00")))
    )
    collected_map = {
        row["charge__patient_id"]: row["collected_total"] for row in collected_by_patient
    }

    hmo_collected_by_patient = (
        PaymentAllocation.objects.filter(charge__in=charge_scope)
        .filter(
            payment__payment_source="HMO",
        )
        .filter(Q(payment__facility_hmo_id=fhmo.id) | Q(payment__system_hmo_id=fhmo.system_hmo_id))
        .values("charge__patient_id")
        .annotate(hmo_collected_total=Coalesce(Sum("amount"), Decimal("0.00")))
    )
    hmo_collected_map = {
        row["charge__patient_id"]: row["hmo_collected_total"] for row in hmo_collected_by_patient
    }

    patient_summary = []
    for row in patient_rows:
        pid = row["patient_id"]
        billed = Decimal(row.get("charges_total") or 0)
        collected = Decimal(collected_map.get(pid) or 0)
        hmo_collected = Decimal(hmo_collected_map.get(pid) or 0)
        patient_summary.append(
            {
                "patient_id": pid,
                "patient_name": f"{row.get('patient__first_name','')} {row.get('patient__last_name','')}".strip(),
                "charge_count": row.get("charge_count") or 0,
                "charges_total": billed,
                "collected_total": collected,
                "hmo_collected_total": hmo_collected,
                "outstanding_total": billed - collected,
            }
        )

    return {
        "brand": brand(),
        "header": header_info,
        "generated_at": timezone.now(),
        "title": f"HMO Statement — {fhmo.system_hmo.name}",
        "facility_hmo": fhmo,
        "facility": getattr(fhmo, "facility", None),
        "provider_profile": provider_profile,
        "charges": charges,
        "payments": payments,
        "patient_summary": patient_summary,
        "charges_total": charges_total,
        "allocated_total": allocated_total,
        "hmo_allocated_total": hmo_allocated_total,
        "other_allocated_total": other_allocated_total,
        "outstanding_total": outstanding_total,
        "period": {"start": start, "end": end},
    }