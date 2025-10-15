from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from facilities.models import Facility
from patients.models import Patient
from .enums import OrderStatus, Priority, Flag

class LabTest(models.Model):
    code = models.CharField(max_length=40, unique=True)   # e.g., FBC_HB
    name = models.CharField(max_length=160)
    unit = models.CharField(max_length=40, blank=True)    # e.g., g/dL
    ref_low = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    ref_high = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"

class LabOrder(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="lab_orders")
    facility = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.SET_NULL, related_name="lab_orders")
    ordered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="lab_orders_created")

    priority = models.CharField(max_length=12, choices=Priority.choices, default=Priority.ROUTINE)
    status = models.CharField(max_length=12, choices=OrderStatus.choices, default=OrderStatus.PENDING)
    ordered_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True)

    encounter_id = models.PositiveIntegerField(null=True, blank=True)  # optional back-link (from Encounters)
    external_lab_name = models.CharField(max_length=160, blank=True)   # if referred outside

    class Meta:
        indexes = [
            models.Index(fields=["patient","ordered_at"]),
            models.Index(fields=["facility","ordered_at"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-ordered_at","-id"]

    def __str__(self):
        return f"LabOrder#{self.id} P:{self.patient_id}"

class LabOrderItem(models.Model):
    order = models.ForeignKey(LabOrder, on_delete=models.CASCADE, related_name="items")
    test = models.ForeignKey(LabTest, on_delete=models.PROTECT)

    sample_collected_at = models.DateTimeField(null=True, blank=True)
    result_value = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    result_text = models.TextField(blank=True)
    result_unit = models.CharField(max_length=40, blank=True)
    ref_low = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    ref_high = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    flag = models.CharField(max_length=6, choices=Flag.choices, blank=True)

    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="lab_results_completed")

    class Meta:
        unique_together = ("order","test")

    def auto_flag(self):
        # prefer item-specific ranges; fallback to test default
        lo = self.ref_low if self.ref_low is not None else self.test.ref_low
        hi = self.ref_high if self.ref_high is not None else self.test.ref_high
        val = self.result_value
        if val is None or lo is None or hi is None:
            return ""
        if val < lo:
            return Flag.LOW
        if val > hi:
            # mark critical if 20% beyond range as naive rule
            if hi and val > (Decimal("1.2") * hi):
                return Flag.CRIT
            return Flag.HIGH
        return Flag.NORMAL

    def save(self, *args, **kwargs):
        if not self.result_unit:
            self.result_unit = self.test.unit or ""
        # snapshot ref range if not provided
        if self.ref_low is None:
            self.ref_low = self.test.ref_low
        if self.ref_high is None:
            self.ref_high = self.test.ref_high
        # compute flag if numeric
        if self.result_value is not None:
            self.flag = self.auto_flag()
        super().save(*args, **kwargs)
