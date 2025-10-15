from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from facilities.models import Facility
from patients.models import Patient
from .enums import SeverityFlag

def _bmi(weight_kg: Decimal|None, height_cm: Decimal|None):
    if not weight_kg or not height_cm or height_cm == 0:
        return None
    h_m = height_cm / Decimal("100")
    return (weight_kg / (h_m * h_m)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def _flag_bp(sys: int|None, dia: int|None):
    if sys is None or dia is None:
        return None
    # Simple thresholds (can refine later to JNC/ESC classes)
    if sys >= 160 or dia >= 100:
        return SeverityFlag.RED
    if sys >= 140 or dia >= 90:
        return SeverityFlag.YELLOW
    return SeverityFlag.GREEN

def _flag_temp_c(t: Decimal|None):
    if t is None:
        return None
    if t >= Decimal("39.0") or t <= Decimal("35.0"):
        return SeverityFlag.RED
    if t >= Decimal("37.5"):
        return SeverityFlag.YELLOW
    return SeverityFlag.GREEN

def _flag_spo2(p: int|None):
    if p is None:
        return None
    if p < 90:
        return SeverityFlag.RED
    if p < 94:
        return SeverityFlag.YELLOW
    return SeverityFlag.GREEN

def _aggregate_flag(*flags: str|None):
    # overall = max severity
    order = {SeverityFlag.GREEN: 0, SeverityFlag.YELLOW: 1, SeverityFlag.RED: 2}
    best = SeverityFlag.GREEN
    for f in flags:
        if not f: 
            continue
        if order.get(f, 0) > order[best]:
            best = f
    return best

class VitalSign(models.Model):
    patient     = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="vitals")
    facility    = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.SET_NULL, related_name="vitals")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    # When measured
    measured_at = models.DateTimeField(help_text="When vitals were taken")

    # Measurements
    systolic   = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(40), MaxValueValidator(300)])
    diastolic  = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(20), MaxValueValidator(200)])
    heart_rate = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(20), MaxValueValidator(250)])
    temp_c     = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)  # e.g. 36.7
    resp_rate  = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(5), MaxValueValidator(80)])
    spo2       = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(50), MaxValueValidator(100)])

    weight_kg  = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.0"))])
    height_cm  = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.0"))])
    bmi        = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # Flags
    bp_flag    = models.CharField(max_length=8, choices=SeverityFlag.choices, null=True, blank=True)
    temp_flag  = models.CharField(max_length=8, choices=SeverityFlag.choices, null=True, blank=True)
    spo2_flag  = models.CharField(max_length=8, choices=SeverityFlag.choices, null=True, blank=True)
    overall    = models.CharField(max_length=8, choices=SeverityFlag.choices, default=SeverityFlag.GREEN)

    notes      = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["patient", "measured_at"]),
            models.Index(fields=["facility", "measured_at"]),
        ]
        ordering = ["-measured_at", "-id"]

    def save(self, *args, **kwargs):
        # Default facility from patient if blank
        if not self.facility and self.patient.facility_id:
            self.facility_id = self.patient.facility_id
        # Derived fields
        self.bmi = _bmi(self.weight_kg, self.height_cm)
        self.bp_flag   = _flag_bp(self.systolic, self.diastolic)
        self.temp_flag = _flag_temp_c(self.temp_c)
        self.spo2_flag = _flag_spo2(self.spo2)
        self.overall   = _aggregate_flag(self.bp_flag, self.temp_flag, self.spo2_flag)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Vitals({self.patient_id}) @ {self.measured_at:%Y-%m-%d %H:%M}"
