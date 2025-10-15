# reports/services/context.py
from decimal import Decimal
from django.apps import apps
from django.conf import settings
from django.utils import timezone

def _model(app_label: str, model_name: str):
    # Lazy model resolver to avoid import-time errors
    return apps.get_model(app_label, model_name)

def brand():
    return getattr(settings, "REPORTS_BRAND", {})

def encounter_context(encounter_id: int) -> dict:
    Encounter = _model("encounters", "Encounter")
    enc = (Encounter.objects
           .select_related("patient", "facility", "created_by")
           .get(id=encounter_id))
    # Optional relateds – use getattr to avoid hard coupling
    vitals = getattr(enc, "vitals_snapshot", None)
    notes = getattr(enc, "notes", "")
    diagnoses = getattr(enc, "diagnoses", []) or []
    plans = getattr(enc, "plans", []) or []
    prescriptions = getattr(enc, "prescriptions", None)
    lab_orders = getattr(enc, "lab_orders", None)
    imaging_requests = getattr(enc, "imaging_requests", None)

    return {
        "brand": brand(),
        "generated_at": timezone.now(),
        "encounter": enc,
        "patient": enc.patient,
        "facility": enc.facility,
        "author": enc.created_by,
        "vitals": vitals,
        "notes": notes,
        "diagnoses": diagnoses,
        "plans": plans,
        "prescriptions": list(prescriptions.all()) if hasattr(prescriptions, "all") else [],
        "labs": list(lab_orders.all()) if hasattr(lab_orders, "all") else [],
        "imaging": list(imaging_requests.all()) if hasattr(imaging_requests, "all") else [],
    }

def lab_context(order_id: int) -> dict:
    LabOrder = _model("labs", "LabOrder")
    order = (LabOrder.objects
             .select_related("patient", "facility", "ordered_by")
             .prefetch_related("results")
             .get(id=order_id))
    # `results` may be a reverse relation with different name in your app.
    results_rel = getattr(order, "results", None)
    results = list(results_rel.all()) if hasattr(results_rel, "all") else []

    return {
        "brand": brand(),
        "generated_at": timezone.now(),
        "order": order,
        "patient": order.patient,
        "facility": order.facility,
        "ordered_by": getattr(order, "ordered_by", None),
        "results": results,
        "status": getattr(order, "status", ""),
        "indication": getattr(order, "indication", ""),
    }

def imaging_context(request_id: int) -> dict:
    ImagingRequest = _model("imaging", "ImagingRequest")
    req = (ImagingRequest.objects
           .select_related("patient", "facility", "requested_by", "procedure", "report")
           .get(id=request_id))
    report = getattr(req, "report", None)
    assets = getattr(report, "assets", None)
    images = list(assets.all()) if hasattr(assets, "all") else []

    return {
        "brand": brand(),
        "generated_at": timezone.now(),
        "request": req,
        "patient": req.patient,
        "facility": req.facility,
        "procedure": getattr(req, "procedure", None),
        "report": report,
        "images": images,
    }

def billing_context(patient_id: int, *, start=None, end=None) -> dict:
    Charge = _model("billing", "Charge")
    Payment = _model("billing", "Payment")
    Patient = _model("patients", "Patient")

    charges = Charge.objects.select_related("service", "facility", "patient").filter(patient_id=patient_id)
    payments = Payment.objects.select_related("facility", "patient").filter(patient_id=patient_id)

    if start:
        charges = charges.filter(created_at__gte=start)
        payments = payments.filter(received_at__gte=start)
    if end:
        charges = charges.filter(created_at__lte=end)
        payments = payments.filter(received_at__lte=end)

    total_charges = sum((c.amount for c in charges), Decimal("0"))
    total_payments = sum((p.amount for p in payments), Decimal("0"))
    balance = total_charges - total_payments

    patient = Patient.objects.select_related("facility").get(id=patient_id)
    return {
        "brand": brand(),
        "generated_at": timezone.now(),
        "patient": patient,
        "facility": getattr(patient, "facility", None),
        "charges": charges.order_by("created_at"),
        "payments": payments.order_by("received_at"),
        "total_charges": total_charges,
        "total_payments": total_payments,
        "balance": balance,
        "period": {"start": start, "end": end},
    }
