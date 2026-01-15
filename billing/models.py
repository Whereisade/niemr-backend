from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from facilities.models import Facility
from patients.models import Patient, HMO
from .enums import ChargeStatus, PaymentMethod, PaymentSource

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
    A payment (or credit) applied to patient or HMO account; linked to one or more charges via PaymentAllocation.
    
    Payment Sources:
    - PATIENT_DIRECT: Individual patient paying directly for their own charges
    - HMO: Health Maintenance Organization paying for multiple patients' charges
    - INSURANCE: Insurance company payment
    - CORPORATE: Corporate/employer payment
    
    For HMO payments:
    - patient is NULL
    - hmo is set
    - payment_source is HMO
    - Can be allocated to charges from multiple patients under that HMO
    
    For patient direct payments:
    - patient is set
    - hmo is NULL (or matches patient's HMO if they have one)
    - payment_source is PATIENT_DIRECT
    """
    # Who is paying
    patient  = models.ForeignKey(
        Patient, 
        null=True,  # NULL for HMO bulk payments
        blank=True,
        on_delete=models.CASCADE, 
        related_name="payments"
    )
    hmo = models.ForeignKey(
        HMO,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payments",
        help_text="Set for HMO bulk payments"
    )
    
    # Scope
    facility = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.CASCADE, related_name="payments")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="payments_owned")
    
    # Payment details
    payment_source = models.CharField(
        max_length=32,
        choices=PaymentSource.choices,
        default=PaymentSource.PATIENT_DIRECT,
        help_text="Source of the payment"
    )
    amount   = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0.01)])
    method   = models.CharField(max_length=16, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = models.CharField(max_length=64, blank=True, help_text="Receipt number, transaction ID, etc.")
    note      = models.CharField(max_length=255, blank=True)
    
    # Metadata
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="payments_received")
    received_at = models.DateTimeField(auto_now_add=True)
    
    # Period for HMO payments (optional)
    period_start = models.DateField(
        null=True,
        blank=True,
        help_text="Start of billing period for HMO bulk payments"
    )
    period_end = models.DateField(
        null=True,
        blank=True,
        help_text="End of billing period for HMO bulk payments"
    )

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
            # Ensure either patient or HMO is set (but not necessarily both)
            models.CheckConstraint(
                name="billing_payment_patient_or_hmo",
                check=(
                    Q(patient__isnull=False) | Q(hmo__isnull=False)
                ),
            ),
        ]
        indexes = [
            models.Index(fields=["hmo", "-received_at"]),
            models.Index(fields=["payment_source", "-received_at"]),
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
    Facility/Provider + HMO specific override price for a service code.
    
    UPDATED: Now references SystemHMO instead of facility-scoped HMO.
    
    Pricing resolution order:
    1. HMOPrice with matching tier (most specific)
    2. HMOPrice without tier (HMO-level default)
    3. Facility/Provider Price (facility default)
    4. Service.default_price (system default)
    
    Scope rules:
    - Facility-based: facility is set, owner is null
    - Independent provider: owner is set, facility is null
    """
    
    # Scope: either facility or independent provider
    facility = models.ForeignKey(
        'facilities.Facility',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='hmo_prices',
    )
    
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='hmo_prices',
        help_text='Independent provider who set this price'
    )
    
    # HMO reference (now SystemHMO)
    system_hmo = models.ForeignKey(
        'patients.SystemHMO',
        on_delete=models.CASCADE,
        related_name='prices',
    )
    
    # Optional tier-specific pricing
    tier = models.ForeignKey(
        'patients.HMOTier',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='prices',
        help_text='If set, this price only applies to this specific tier'
    )
    
    # Service reference
    service = models.ForeignKey(
        'billing.Service',
        on_delete=models.CASCADE,
        related_name='hmo_prices',
    )
    
    # Pricing
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    
    currency = models.CharField(max_length=8, default='NGN')
    
    is_active = models.BooleanField(default=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['service__code']
        verbose_name = 'HMO Price'
        verbose_name_plural = 'HMO Prices'
        constraints = [
            # Either facility or owner must be set (XOR)
            models.CheckConstraint(
                name='billing_hmo_price_scope_xor',
                check=(
                    (models.Q(facility__isnull=False) & models.Q(owner__isnull=True))
                    | (models.Q(facility__isnull=True) & models.Q(owner__isnull=False))
                ),
            ),
            # Unique price per facility + system_hmo + tier + service
            models.UniqueConstraint(
                name='unique_facility_hmo_tier_service_price',
                fields=['facility', 'system_hmo', 'tier', 'service'],
                condition=models.Q(facility__isnull=False),
            ),
            # Unique price per owner + system_hmo + tier + service
            models.UniqueConstraint(
                name='unique_owner_hmo_tier_service_price',
                fields=['owner', 'system_hmo', 'tier', 'service'],
                condition=models.Q(owner__isnull=False),
            ),
        ]
        indexes = [
            models.Index(fields=['facility', 'system_hmo', 'service']),
            models.Index(fields=['owner', 'system_hmo', 'service']),
            models.Index(fields=['system_hmo', 'tier', 'service']),
        ]
    
    def __str__(self):
        tier_str = f' ({self.tier.name})' if self.tier else ''
        scope = self.facility.name if self.facility else f'Provider:{self.owner_id}'
        return f'{scope} - {self.system_hmo.name}{tier_str}: {self.service.code} @ {self.amount}'