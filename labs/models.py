from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from facilities.models import Facility
from patients.models import Patient

from .enums import OrderStatus, Priority, Flag


class LabTest(models.Model):
    """
    Lab test catalog item.
    
    Scoping:
    - facility: If set, test belongs to this facility's catalog
    - created_by: If set (and facility is null), test belongs to this independent lab user
    - A test with both null is a "global" test (legacy/admin-created)
    """
    code = models.CharField(max_length=40)  # e.g., FBC_HB - unique per facility/user
    name = models.CharField(max_length=160)
    unit = models.CharField(max_length=40, blank=True)  # e.g., g/dL
    ref_low = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    ref_high = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    is_active = models.BooleanField(default=True)
    
    # Scoping fields
    facility = models.ForeignKey(
        Facility,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="lab_tests",
        help_text="Facility this test belongs to (null for independent labs)"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_lab_tests",
        help_text="Creator (used for independent lab scoping when facility is null)"
    )

    class Meta:
        # Unique code per facility OR per independent user
        constraints = [
            models.UniqueConstraint(
                fields=["code", "facility"],
                name="unique_labtest_code_per_facility",
                condition=models.Q(facility__isnull=False),
            ),
            models.UniqueConstraint(
                fields=["code", "created_by"],
                name="unique_labtest_code_per_user",
                condition=models.Q(facility__isnull=True, created_by__isnull=False),
            ),
        ]
        indexes = [
            models.Index(fields=["facility", "is_active"]),
            models.Index(fields=["created_by", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class LabOrder(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="lab_orders")
    facility = models.ForeignKey(
        Facility, null=True, blank=True, on_delete=models.SET_NULL, related_name="lab_orders"
    )
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="lab_orders_created",
    )

    priority = models.CharField(max_length=12, choices=Priority.choices, default=Priority.ROUTINE)
    status = models.CharField(max_length=12, choices=OrderStatus.choices, default=OrderStatus.PENDING)
    ordered_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True)

    encounter_id = models.PositiveIntegerField(null=True, blank=True)  # optional back-link (from Encounters)
    external_lab_name = models.CharField(max_length=160, blank=True)  # if referred outside

    # ✅ Outsourcing: assign to an independent LAB user (facility=None)
    outsourced_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lab_orders_assigned",
    )

    class Meta:
        indexes = [
            models.Index(fields=["patient", "ordered_at"]),
            models.Index(fields=["facility", "ordered_at"]),
            models.Index(fields=["outsourced_to", "ordered_at"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-ordered_at", "-id"]

    def __str__(self):
        return f"LabOrder#{self.id} P:{self.patient_id}"


class LabOrderItem(models.Model):
    order = models.ForeignKey(LabOrder, on_delete=models.CASCADE, related_name="items")
    # ✅ Either catalog test OR manual typed request
    test = models.ForeignKey(LabTest, null=True, blank=True, on_delete=models.PROTECT)
    requested_name = models.CharField(max_length=160, blank=True)

    sample_collected_at = models.DateTimeField(null=True, blank=True)
    result_value = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    result_text = models.TextField(blank=True)
    result_unit = models.CharField(max_length=40, blank=True)
    ref_low = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    ref_high = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    flag = models.CharField(max_length=6, choices=Flag.choices, blank=True)

    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lab_results_completed",
    )

    class Meta:
        constraints = [
            # Ensure one catalog test appears only once per order; allow multiple manual entries.
            models.UniqueConstraint(
                fields=["order", "test"],
                condition=models.Q(test__isnull=False),
                name="uniq_laborderitem_order_test_when_test_present",
            )
        ]

    @property
    def display_name(self) -> str:
        if self.test_id:
            return self.test.name
        return (self.requested_name or "").strip()

    def auto_flag(self):
        # Only auto-flag when numeric + ref ranges exist
        if self.result_value is None:
            return ""

        lo = self.ref_low
        hi = self.ref_high
        if lo is None or hi is None:
            # fallback to test defaults if available
            if self.test_id:
                lo = self.test.ref_low
                hi = self.test.ref_high

        if lo is None or hi is None:
            return ""

        val = self.result_value
        if val < lo:
            return Flag.LOW
        if val > hi:
            # mark critical if 20% beyond range as naive rule
            if hi and val > (Decimal("1.2") * hi):
                return Flag.CRIT
            return Flag.HIGH
        return Flag.NORMAL

    def save(self, *args, **kwargs):
        # snapshot defaults when test exists
        if self.test_id:
            if not self.result_unit:
                self.result_unit = self.test.unit or ""
            if self.ref_low is None:
                self.ref_low = self.test.ref_low
            if self.ref_high is None:
                self.ref_high = self.test.ref_high

        if self.result_value is not None:
            self.flag = self.auto_flag()

        super().save(*args, **kwargs)