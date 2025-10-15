from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from facilities.models import Facility
from patients.models import Patient
from .enums import ChargeStatus, PaymentMethod

class Service(models.Model):
    """
    Billable service catalog (consultation, lab test, imaging, dispensing, etc.)
    External modules reference via 'code' to get a default price.
    """
    code = models.CharField(max_length=64, unique=True)   # e.g., CONSULT_STD, LAB:FBC_HB, IMG:CXR, DRUG:PARA_500_TAB
    name = models.CharField(max_length=255)
    default_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)], default=0)
    is_active = models.BooleanField(default=True)
    def __str__(self): return f"{self.code} - {self.name}"

class Price(models.Model):
    """
    Facility override for a Service price.
    """
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="prices")
    service  = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="prices")
    amount   = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=8, default="NGN")

    class Meta:
        unique_together = ("facility","service")

class Charge(models.Model):
    """
    A line item charge against a patient (e.g., 'FBC test', 'CXR', 'Consultation', 'Paracetamol 10 tabs').
    """
    patient  = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="charges")
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="charges")
    service  = models.ForeignKey(Service, on_delete=models.PROTECT)
    description = models.CharField(max_length=255, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    qty = models.PositiveIntegerField(default=1)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])  # unit_price * qty
    status = models.CharField(max_length=16, choices=ChargeStatus.choices, default=ChargeStatus.UNPAID)

    # optional back-links to operational modules
    encounter_id = models.PositiveIntegerField(null=True, blank=True)
    lab_order_id = models.PositiveIntegerField(null=True, blank=True)
    imaging_request_id = models.PositiveIntegerField(null=True, blank=True)
    prescription_id = models.PositiveIntegerField(null=True, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="charges_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["patient","created_at"]),
            models.Index(fields=["facility","created_at"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-created_at","-id"]

    def __str__(self): return f"Charge#{self.id} {self.service.code} x{self.qty}"

class Payment(models.Model):
    """
    A payment (or credit) applied to a patient account; linked to one or more charges via PaymentAllocation.
    """
    patient  = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="payments")
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="payments")
    amount   = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0.01)])
    method   = models.CharField(max_length=16, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = models.CharField(max_length=64, blank=True)  # receipt, POS ref, bank ref, etc.
    note      = models.CharField(max_length=255, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at","-id"]

class PaymentAllocation(models.Model):
    """
    Many-to-many allocation of a payment to specific charges.
    """
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="allocations")
    charge  = models.ForeignKey(Charge, on_delete=models.CASCADE, related_name="allocations")
    amount  = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])

    class Meta:
        unique_together = ("payment","charge")
