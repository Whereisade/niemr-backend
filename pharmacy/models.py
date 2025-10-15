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
    """
    code = models.CharField(max_length=64, unique=True)  # e.g., PARA_500_TAB
    name = models.CharField(max_length=255)
    strength = models.CharField(max_length=64, blank=True)  # e.g., 500mg
    form = models.CharField(max_length=64, blank=True)      # Tab/Syrup/Injection
    route = models.CharField(max_length=64, blank=True)     # PO/IM/IV/SC
    qty_per_unit = models.PositiveIntegerField(default=1)   # tablets per pack, etc.
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)

    def __str__(self): return f"{self.code} - {self.name} {self.strength} {self.form}"

class StockItem(models.Model):
    """
    Stock per facility per drug. Keep it simple; one row aggregates current_qty.
    """
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="stock_items")
    drug = models.ForeignKey(Drug, on_delete=models.CASCADE, related_name="stock_items")
    current_qty = models.PositiveIntegerField(default=0)  # base unit (e.g., tablets)

    class Meta:
        unique_together = ("facility","drug")

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
    prescribed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="prescribed_by")
    encounter_id = models.PositiveIntegerField(null=True, blank=True)  # optional link to Encounters
    status = models.CharField(max_length=24, choices=RxStatus.choices, default=RxStatus.PRESCRIBED)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["patient","created_at"]),
            models.Index(fields=["facility","created_at"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-created_at","-id"]

class PrescriptionItem(models.Model):
    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE, related_name="items")
    drug = models.ForeignKey(Drug, on_delete=models.PROTECT)
    dose = models.CharField(max_length=64)              # e.g., "500 mg", "10 mL"
    frequency = models.CharField(max_length=64)         # e.g., "bd", "tds", "q8h"
    duration_days = models.PositiveIntegerField(default=1)
    qty_prescribed = models.PositiveIntegerField(default=0)  # base unit (tabs, mL)
    qty_dispensed = models.PositiveIntegerField(default=0)
    instruction = models.CharField(max_length=255, blank=True)

    def remaining(self) -> int:
        return max(self.qty_prescribed - self.qty_dispensed, 0)

class DispenseEvent(models.Model):
    """
    Each dispensing action (partial or full) for traceability.
    """
    prescription_item = models.ForeignKey(PrescriptionItem, on_delete=models.CASCADE, related_name="dispenses")
    qty = models.PositiveIntegerField()  # base unit
    dispensed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    dispensed_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True)
