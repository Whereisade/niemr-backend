from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

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
    default_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)], null=True, blank=True, default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self): return f"{self.code} - {self.name}"

class Price(models.Model):
    """
    Price override for a Service.
    - Facility-linked pricing: facility is set, owner is null
    - Independent pricing: owner is set, facility is null
    """
    facility = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.CASCADE, related_name="prices")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="prices")
    service  = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="prices")
    amount   = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=8, default="NGN")

    class Meta:
        constraints = [
            models.CheckConstraint(
                name="billing_price_scope_xor",
                check=(
                    (Q(facility__isnull=False) & Q(owner__isnull=True))
                    | (Q(facility__isnull=True) & Q(owner__isnull=False))
                ),
            ),
            models.UniqueConstraint(
                name="billing_price_unique_facility_service",
                fields=["facility", "service"],
                condition=Q(facility__isnull=False, owner__isnull=True),
            ),
            models.UniqueConstraint(
                name="billing_price_unique_owner_service",
                fields=["owner", "service"],
                condition=Q(owner__isnull=False, facility__isnull=True),
            ),
        ]

class Charge(models.Model):
    """
    A line item charge against a patient (e.g., 'FBC test', 'CXR', 'Consultation', 'Paracetamol 10 tabs').
    """
    patient  = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="charges")
    facility = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.CASCADE, related_name="charges")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="charges_owned")
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
            models.Index(fields=["owner","created_at"]),
            models.Index(fields=["status"]),
        ]
        constraints = [
            models.CheckConstraint(
                name="billing_charge_scope_xor",
                check=(
                    (Q(facility__isnull=False) & Q(owner__isnull=True))
                    | (Q(facility__isnull=True) & Q(owner__isnull=False))
                ),
            ),
        ]
        ordering = ["-created_at","-id"]

    def __str__(self): return f"Charge#{self.id} {self.service.code} x{self.qty}"

class Payment(models.Model):
    """
    A payment (or credit) applied to a patient account; linked to one or more charges via PaymentAllocation.
    """
    patient  = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="payments")
    facility = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.CASCADE, related_name="payments")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="payments_owned")
    amount   = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0.01)])
    method   = models.CharField(max_length=16, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = models.CharField(max_length=64, blank=True)  # receipt, POS ref, bank ref, etc.
    note      = models.CharField(max_length=255, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at","-id"]
        constraints = [
            models.CheckConstraint(
                name="billing_payment_scope_xor",
                check=(
                    (Q(facility__isnull=False) & Q(owner__isnull=True))
                    | (Q(facility__isnull=True) & Q(owner__isnull=False))
                ),
            ),
        ]

class PaymentAllocation(models.Model):
    """
    Many-to-many allocation of a payment to specific charges.
    """
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="allocations")
    charge  = models.ForeignKey(Charge, on_delete=models.CASCADE, related_name="allocations")
    amount  = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])

    class Meta:
        unique_together = ("payment","charge")
class HMOPrice(models.Model):
    """
    Facility + HMO specific override price for a service code.

    Used when a patient is insured (insurance_status=INSURED) and attached to an
    HMO within the same facility. Falls back to the facility Price if no override.
    """
    facility = models.ForeignKey(
        Facility,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="hmo_prices",
    )
    hmo = models.ForeignKey(
        "patients.HMO",
        on_delete=models.CASCADE,
        related_name="prices",
    )
    service = models.ForeignKey(Service, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=8, default="NGN")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["service__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["facility", "hmo", "service"],
                name="uniq_hmo_price_per_service",
            )
        ]

    def __str__(self):
        return f"{self.hmo_id}:{self.service.code} {self.amount} {self.currency}"



