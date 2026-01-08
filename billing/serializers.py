from decimal import Decimal

from django.db import models, transaction
from rest_framework import serializers

from .models import Service, Price, Charge, Payment, PaymentAllocation, HMOPrice
from .enums import ChargeStatus, PaymentMethod, PaymentSource
from .services.pricing import resolve_price


# --- Catalog & Price ---
class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ["id", "code", "name", "default_price", "is_active"]


class PriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Price
        fields = ["id", "facility", "owner", "service", "amount", "currency"]


# --- Charges ---
class ChargeCreateSerializer(serializers.ModelSerializer):
    service_code = serializers.CharField(write_only=True)

    class Meta:
        model = Charge
        fields = [
            "id",
            "patient",
            "service_code",
            "description",
            "qty",
            "encounter_id",
            "lab_order_id",
            "imaging_request_id",
            "prescription_id",
        ]

    def validate(self, attrs):
        try:
            service = Service.objects.get(code=attrs["service_code"], is_active=True)
        except Service.DoesNotExist:
            raise serializers.ValidationError("Unknown/inactive service_code")
        attrs["service"] = service
        return attrs

    @transaction.atomic
    def create(self, validated):
        user = self.context["request"].user
        patient = validated["patient"]

        # Billing scope:
        # - Facility-linked users bill the facility.
        # - Admins without a facility bill the patient's facility (if any).
        # - Independent users bill themselves (owner billing).
        facility = getattr(user, "facility", None) if getattr(user, "facility_id", None) else None
        owner = None
        role = (getattr(user, "role", "") or "").upper()

        if not facility and role in {"ADMIN", "SUPER_ADMIN"} and getattr(patient, "facility_id", None):
            facility = patient.facility
        if not facility:
            owner = user

        unit_price = resolve_price(service=validated["service"], facility=facility, owner=owner)
        qty = validated.get("qty") or 1
        amount = unit_price * qty

        return Charge.objects.create(
            patient=patient,
            facility=facility,
            owner=owner,
            service=validated["service"],
            description=validated.get("description", ""),
            unit_price=unit_price,
            qty=qty,
            amount=amount,
            created_by=user,
            encounter_id=validated.get("encounter_id"),
            lab_order_id=validated.get("lab_order_id"),
            imaging_request_id=validated.get("imaging_request_id"),
            prescription_id=validated.get("prescription_id"),
        )


class ChargeReadSerializer(serializers.ModelSerializer):
    service_code = serializers.CharField(source="service.code", read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True)
    patient_name = serializers.SerializerMethodField()
    patient_hmo = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()

    # annotated by ChargeViewSet.get_queryset
    allocated_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = Charge
        fields = [
            "id",
            "patient",
            "patient_name",
            "patient_hmo",
            "facility",
            "owner",
            "service",
            "service_code",
            "service_name",
            "description",
            "unit_price",
            "qty",
            "amount",
            "allocated_total",
            "outstanding",
            "status",
            "encounter_id",
            "lab_order_id",
            "imaging_request_id",
            "prescription_id",
            "created_by",
            "created_by_name",
            "created_at",
        ]

    def get_patient_name(self, obj):
        p = getattr(obj, "patient", None)
        if not p:
            return ""
        name = f"{getattr(p, 'first_name', '')} {getattr(p, 'last_name', '')}".strip()
        return name or str(p)

    def get_patient_hmo(self, obj):
        """Return patient's HMO info if they have one"""
        p = getattr(obj, "patient", None)
        if not p:
            return None
        hmo = getattr(p, "hmo", None)
        if not hmo:
            return None
        return {
            "id": hmo.id,
            "name": hmo.name,
        }

    def get_created_by_name(self, obj):
        u = getattr(obj, "created_by", None)
        if not u:
            return ""
        full = (getattr(u, "get_full_name", None) and u.get_full_name()) or ""
        return full.strip() or getattr(u, "email", "") or str(u)

    def get_outstanding(self, obj):
        amt = Decimal(str(getattr(obj, "amount", 0) or 0))
        alloc = Decimal(str(getattr(obj, "allocated_total", 0) or 0))
        return amt - alloc


# --- Payments ---
class PaymentAllocationReadSerializer(serializers.ModelSerializer):
    charge_id = serializers.IntegerField(source="charge.id", read_only=True)
    charge_service_code = serializers.CharField(source="charge.service.code", read_only=True)
    charge_description = serializers.CharField(source="charge.description", read_only=True)
    patient_name = serializers.SerializerMethodField()

    class Meta:
        model = PaymentAllocation
        fields = ["charge_id", "charge_service_code", "charge_description", "patient_name", "amount"]

    def get_patient_name(self, obj):
        p = getattr(obj.charge, "patient", None)
        if not p:
            return ""
        return f"{p.first_name} {p.last_name}".strip()


class PaymentCreateSerializer(serializers.ModelSerializer):
    # Option A: pay a single charge
    charge_id = serializers.IntegerField(write_only=True, required=False)

    # Option B: allocate across many charges
    allocations = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField()),
        write_only=True,
        required=False,
        help_text="Optional list of {charge_id, amount}. If omitted, we'll auto-allocate to oldest unpaid charges.",
    )

    class Meta:
        model = Payment
        fields = ["id", "patient", "amount", "method", "reference", "note", "charge_id", "allocations"]

    def validate_method(self, v):
        if not v:
            return PaymentMethod.CASH
        return v

    @transaction.atomic
    def create(self, validated):
        from .services.rollup import recompute_charge_status

        user = self.context["request"].user
        patient = validated["patient"]
        role = (getattr(user, "role", "") or "").upper()

        facility = getattr(user, "facility", None) if getattr(user, "facility_id", None) else None
        owner = None
        if not facility and role in {"ADMIN", "SUPER_ADMIN"} and getattr(patient, "facility_id", None):
            facility = patient.facility
        if not facility:
            owner = user

        payment = Payment.objects.create(
            patient=patient,
            facility=facility,
            owner=owner,
            payment_source=PaymentSource.PATIENT_DIRECT,
            amount=validated["amount"],
            method=validated.get("method") or PaymentMethod.CASH,
            reference=validated.get("reference", ""),
            note=validated.get("note", ""),
            received_by=user,
        )

        allocations = validated.get("allocations") or None
        charge_id = validated.get("charge_id") or None

        # Option A: single-charge payment shortcut
        if (not allocations) and charge_id:
            allocations = [{"charge_id": str(charge_id), "amount": str(payment.amount)}]

        # Option B: explicit allocations
        if allocations:
            total_alloc = Decimal("0.00")
            for item in allocations:
                try:
                    cid = int(item.get("charge_id"))
                    amt = Decimal(str(item.get("amount")))
                except Exception:
                    continue
                if amt <= 0:
                    continue

                charge = Charge.objects.select_for_update().get(
                    id=cid,
                    patient=patient,
                    facility=facility,
                    owner=owner,
                )
                # cap to outstanding on that charge
                already = charge.allocations.aggregate(t=models.Sum("amount"))["t"] or Decimal("0.00")
                due = Decimal(str(charge.amount)) - Decimal(str(already))
                take = min(amt, max(due, Decimal("0.00")))

                if take <= 0:
                    recompute_charge_status(charge)
                    continue

                PaymentAllocation.objects.update_or_create(
                    payment=payment, charge=charge, defaults={"amount": take}
                )
                total_alloc += take
                recompute_charge_status(charge)

            return payment

        # Option C: auto-allocation to oldest unpaid charges in this scope
        remaining = Decimal(str(payment.amount))
        charges = (
            Charge.objects.select_for_update()
            .filter(patient=patient, facility=facility, owner=owner)
            .exclude(status=ChargeStatus.VOID)
            .order_by("created_at", "id")
        )

        for ch in charges:
            if remaining <= 0:
                break
            already = ch.allocations.aggregate(t=models.Sum("amount"))["t"] or Decimal("0.00")
            due = Decimal(str(ch.amount)) - Decimal(str(already))
            if due <= 0:
                recompute_charge_status(ch)
                continue
            take = min(remaining, due)
            PaymentAllocation.objects.update_or_create(
                payment=payment, charge=ch, defaults={"amount": take}
            )
            remaining -= take
            recompute_charge_status(ch)

        return payment


class PaymentReadSerializer(serializers.ModelSerializer):
    patient_name = serializers.SerializerMethodField()
    hmo_name = serializers.SerializerMethodField()
    received_by_name = serializers.SerializerMethodField()
    allocations = PaymentAllocationReadSerializer(many=True, read_only=True)
    allocated_total = serializers.SerializerMethodField()
    unallocated_total = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            "id",
            "patient",
            "patient_name",
            "hmo",
            "hmo_name",
            "facility",
            "owner",
            "payment_source",
            "amount",
            "method",
            "reference",
            "note",
            "received_by",
            "received_by_name",
            "received_at",
            "period_start",
            "period_end",
            "allocations",
            "allocated_total",
            "unallocated_total",
        ]

    def get_patient_name(self, obj):
        p = getattr(obj, "patient", None)
        if not p:
            return ""
        name = f"{getattr(p, 'first_name', '')} {getattr(p, 'last_name', '')}".strip()
        return name or str(p)

    def get_hmo_name(self, obj):
        hmo = getattr(obj, "hmo", None)
        if not hmo:
            return ""
        return hmo.name

    def get_received_by_name(self, obj):
        u = getattr(obj, "received_by", None)
        if not u:
            return ""
        full = (getattr(u, "get_full_name", None) and u.get_full_name()) or ""
        return full.strip() or getattr(u, "email", "") or str(u)

    def get_allocated_total(self, obj):
        return sum((a.amount for a in obj.allocations.all()), Decimal("0.00"))

    def get_unallocated_total(self, obj):
        return Decimal(str(obj.amount)) - self.get_allocated_total(obj)


# --- HMO Payment Serializers ---

class HMOPaymentCreateSerializer(serializers.Serializer):
    """
    Create an HMO bulk payment that can be allocated to multiple patients' charges.
    """
    hmo_id = serializers.IntegerField(required=True, help_text="HMO making the payment")
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.TRANSFER)
    reference = serializers.CharField(max_length=64, required=False, allow_blank=True)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)
    period_start = serializers.DateField(required=False, allow_null=True)
    period_end = serializers.DateField(required=False, allow_null=True)
    
    # Allocation options
    allocations = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        help_text="Optional list of {charge_id, amount} to allocate to specific charges"
    )
    auto_allocate = serializers.BooleanField(
        default=True,
        help_text="If true and no allocations provided, auto-allocate to oldest unpaid HMO charges"
    )

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be greater than zero")
        return value

    def validate(self, attrs):
        # Validate period dates
        if attrs.get("period_start") and attrs.get("period_end"):
            if attrs["period_start"] > attrs["period_end"]:
                raise serializers.ValidationError({
                    "period_end": "End date must be after start date"
                })
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        from patients.models import HMO
        from .services.rollup import recompute_charge_status

        user = self.context["request"].user
        facility = getattr(user, "facility", None)
        
        if not facility:
            raise serializers.ValidationError("User must belong to a facility to record HMO payments")

        # Get HMO
        hmo_id = validated_data["hmo_id"]
        try:
            hmo = HMO.objects.get(id=hmo_id, facility=facility)
        except HMO.DoesNotExist:
            raise serializers.ValidationError({"hmo_id": "HMO not found in your facility"})

        # Create payment
        payment = Payment.objects.create(
            hmo=hmo,
            facility=facility,
            payment_source=PaymentSource.HMO,
            amount=validated_data["amount"],
            method=validated_data.get("method", PaymentMethod.TRANSFER),
            reference=validated_data.get("reference", ""),
            note=validated_data.get("note", ""),
            period_start=validated_data.get("period_start"),
            period_end=validated_data.get("period_end"),
            received_by=user,
        )

        # Handle allocations
        allocations = validated_data.get("allocations", [])
        auto_allocate = validated_data.get("auto_allocate", True)

        if allocations:
            # Manual allocations provided
            self._allocate_to_charges(payment, allocations, hmo, facility)
        elif auto_allocate:
            # Auto-allocate to oldest unpaid charges for this HMO
            self._auto_allocate_to_hmo_charges(payment, hmo, facility)

        return payment

    def _allocate_to_charges(self, payment, allocations, hmo, facility):
        """Allocate payment to specific charges"""
        from .services.rollup import recompute_charge_status
        
        remaining = Decimal(str(payment.amount))
        
        for item in allocations:
            try:
                charge_id = int(item.get("charge_id"))
                alloc_amount = Decimal(str(item.get("amount")))
            except (ValueError, TypeError):
                continue
                
            if alloc_amount <= 0:
                continue
                
            try:
                charge = Charge.objects.select_for_update().get(
                    id=charge_id,
                    facility=facility,
                    patient__hmo=hmo
                )
            except Charge.DoesNotExist:
                continue
            
            # Calculate outstanding amount
            already_paid = charge.allocations.aggregate(
                total=models.Sum("amount")
            )["total"] or Decimal("0.00")
            outstanding = Decimal(str(charge.amount)) - already_paid
            
            if outstanding <= 0:
                recompute_charge_status(charge)
                continue
            
            # Allocate up to outstanding amount
            take = min(alloc_amount, outstanding, remaining)
            
            if take > 0:
                PaymentAllocation.objects.create(
                    payment=payment,
                    charge=charge,
                    amount=take
                )
                remaining -= take
                recompute_charge_status(charge)
            
            if remaining <= 0:
                break

    def _auto_allocate_to_hmo_charges(self, payment, hmo, facility):
        """Auto-allocate payment to oldest unpaid charges for this HMO"""
        from .services.rollup import recompute_charge_status
        
        remaining = Decimal(str(payment.amount))
        
        # Get unpaid/partially paid charges for patients under this HMO
        charges = (
            Charge.objects
            .select_for_update()
            .filter(
                facility=facility,
                patient__hmo=hmo,
                patient__hmo__isnull=False,
            )
            .exclude(status=ChargeStatus.PAID)
            .exclude(status=ChargeStatus.VOID)
            .order_by("created_at", "id")
        )
        
        for charge in charges:
            if remaining <= 0:
                break
            
            # Calculate outstanding
            already_paid = charge.allocations.aggregate(
                total=models.Sum("amount")
            )["total"] or Decimal("0.00")
            outstanding = Decimal(str(charge.amount)) - already_paid
            
            if outstanding <= 0:
                recompute_charge_status(charge)
                continue
            
            # Allocate what we can
            take = min(remaining, outstanding)
            
            PaymentAllocation.objects.create(
                payment=payment,
                charge=charge,
                amount=take
            )
            
            remaining -= take
            recompute_charge_status(charge)


class HMOOutstandingChargesSerializer(serializers.Serializer):
    """Summary of outstanding charges for an HMO"""
    total_charges = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_paid = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_outstanding = serializers.DecimalField(max_digits=12, decimal_places=2)
    patient_count = serializers.IntegerField()
    charge_count = serializers.IntegerField()