from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from facilities.models import Facility
from patients.models import Patient
from .enums import RxStatus, TxnType


class Drug(models.Model):
    """
    Catalog line item. 'qty_per_unit' allows packs vs unit dose handling.
    
    Scoping:
    - facility: If set, drug belongs to this facility's catalog
    - created_by: If set (and facility is null), drug belongs to this independent pharmacy user
    - A drug with both null is a "global" drug (legacy/admin-created)
    """
    code = models.CharField(max_length=64)  # e.g., PARA_500_TAB - unique per facility/user
    name = models.CharField(max_length=255)
    strength = models.CharField(max_length=64, blank=True)  # e.g., 500mg
    form = models.CharField(max_length=64, blank=True)      # Tab/Syrup/Injection
    route = models.CharField(max_length=64, blank=True)     # PO/IM/IV/SC
    qty_per_unit = models.PositiveIntegerField(default=1)   # tablets per pack, etc.
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)
    
    # Scoping fields
    facility = models.ForeignKey(
        Facility,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="drugs",
        help_text="Facility this drug belongs to (null for independent pharmacies)"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_drugs",
        help_text="Creator (used for independent pharmacy scoping when facility is null)"
    )

    class Meta:
        # Unique code per facility OR per independent user
        constraints = [
            models.UniqueConstraint(
                fields=["code", "facility"],
                name="unique_drug_code_per_facility",
                condition=models.Q(facility__isnull=False),
            ),
            models.UniqueConstraint(
                fields=["code", "created_by"],
                name="unique_drug_code_per_user",
                condition=models.Q(facility__isnull=True, created_by__isnull=False),
            ),
        ]
        indexes = [
            models.Index(fields=["facility", "is_active"]),
            models.Index(fields=["created_by", "is_active"]),
        ]

    def __str__(self):
        return f"{self.code} - {self.name} {self.strength} {self.form}".strip()


class StockItem(models.Model):
    """
    Stock per facility per drug. Keep it simple; one row aggregates current_qty.
    """
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="stock_items")
    drug = models.ForeignKey(Drug, on_delete=models.CASCADE, related_name="stock_items")
    current_qty = models.PositiveIntegerField(default=0)  # base unit (e.g., tablets)

    class Meta:
        unique_together = ("facility", "drug")


class StockTxn(models.Model):
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE)
    drug = models.ForeignKey(Drug, on_delete=models.CASCADE)
    txn_type = models.CharField(max_length=8, choices=TxnType.choices)
    qty = models.IntegerField()  # positive for IN, negative for OUT
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)


class Prescription(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="prescriptions")
    facility = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.SET_NULL, related_name="prescriptions")
    prescribed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="prescribed_by"
    )

    encounter_id = models.PositiveIntegerField(null=True, blank=True)  # optional link to Encounters
    status = models.CharField(max_length=24, choices=RxStatus.choices, default=RxStatus.PRESCRIBED)
    note = models.TextField(blank=True)

    # ✅ Outsourcing: assign to one independent pharmacy user (facility=None)
    outsourced_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="prescriptions_assigned",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["patient", "created_at"]),
            models.Index(fields=["facility", "created_at"]),
            models.Index(fields=["outsourced_to", "created_at"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Rx#{self.id} P:{self.patient_id}"


class PrescriptionItem(models.Model):
    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE, related_name="items")

    # ✅ catalog drug OR free-text entry
    drug = models.ForeignKey(Drug, null=True, blank=True, on_delete=models.PROTECT)
    drug_name = models.CharField(max_length=255, blank=True)  # free-text if not in catalog

    dose = models.CharField(max_length=64)              # e.g., "500 mg", "10 mL"
    frequency = models.CharField(max_length=64)         # e.g., "bd", "tds", "q8h"
    duration_days = models.PositiveIntegerField(default=1)
    qty_prescribed = models.PositiveIntegerField(default=0)  # base unit (tabs, mL)
    qty_dispensed = models.PositiveIntegerField(default=0)
    instruction = models.CharField(max_length=255, blank=True)

    def remaining(self) -> int:
        return max(self.qty_prescribed - self.qty_dispensed, 0)

    @property
    def display_name(self) -> str:
        if self.drug_id:
            return self.drug.name
        return (self.drug_name or "").strip() or "(Free-text medication)"


class DispenseEvent(models.Model):
    """
    Each dispensing action (partial or full) for traceability.
    """
    prescription_item = models.ForeignKey(PrescriptionItem, on_delete=models.CASCADE, related_name="dispenses")
    qty = models.PositiveIntegerField()  # base unit
    dispensed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    dispensed_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True)