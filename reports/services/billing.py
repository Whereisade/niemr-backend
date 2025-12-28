# reports/services/billing.py (create this file)
from decimal import Decimal
from django.template.loader import render_to_string
from weasyprint import HTML
from billing.models import Charge, Payment, PaymentAllocation
from patients.models import Patient

def generate_billing_receipt(charge_id):
    """Generate PDF receipt for a single charge."""
    try:
        charge = Charge.objects.select_related(
            'patient', 'service', 'facility', 'created_by'
        ).prefetch_related('allocations__payment').get(id=charge_id)
    except Charge.DoesNotExist:
        raise ValueError(f"Charge {charge_id} not found")
    
    # Calculate payment allocations
    allocations = charge.allocations.select_related('payment').all()
    allocated_total = sum(a.amount for a in allocations)
    outstanding = charge.amount - allocated_total
    
    context = {
        'charge': charge,
        'patient': charge.patient,
        'facility': charge.facility,
        'allocations': allocations,
        'allocated_total': allocated_total,
        'outstanding': outstanding,
    }
    
    html_string = render_to_string('reports/billing_receipt.html', context)
    pdf = HTML(string=html_string).write_pdf()
    filename = f"charge_{charge_id}_receipt.pdf"
    
    return pdf, filename


def generate_billing_statement(patient_id, start_date=None, end_date=None):
    """Generate billing statement for a patient."""
    try:
        patient = Patient.objects.select_related('facility').get(id=patient_id)
    except Patient.DoesNotExist:
        raise ValueError(f"Patient {patient_id} not found")
    
    # Get charges
    charges_qs = Charge.objects.filter(patient=patient).exclude(status='VOID')
    if start_date:
        charges_qs = charges_qs.filter(created_at__gte=start_date)
    if end_date:
        charges_qs = charges_qs.filter(created_at__lte=end_date)
    
    charges = charges_qs.select_related('service').prefetch_related(
        'allocations__payment'
    ).order_by('-created_at')
    
    # Get payments
    payments_qs = Payment.objects.filter(patient=patient)
    if start_date:
        payments_qs = payments_qs.filter(received_at__gte=start_date)
    if end_date:
        payments_qs = payments_qs.filter(received_at__lte=end_date)
    
    payments = payments_qs.prefetch_related('allocations__charge').order_by('-received_at')
    
    # Calculate totals
    charges_total = sum(c.amount for c in charges)
    payments_total = sum(p.amount for p in payments)
    balance = charges_total - payments_total
    
    context = {
        'patient': patient,
        'charges': charges,
        'payments': payments,
        'charges_total': charges_total,
        'payments_total': payments_total,
        'balance': balance,
        'start_date': start_date,
        'end_date': end_date,
        'facility': patient.facility,
    }
    
    html_string = render_to_string('reports/billing_statement.html', context)
    pdf = HTML(string=html_string).write_pdf()
    filename = f"billing_statement_{patient_id}.pdf"
    
    return pdf, filename