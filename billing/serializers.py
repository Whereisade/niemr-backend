from decimal import Decimal

from django.db import models, transaction
from rest_framework import serializers

from .models import Service, Price, Charge, Payment, PaymentAllocation
from .enums import ChargeStatus, PaymentMethod
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

    class Meta:
        model = PaymentAllocation
        fields = ["charge_id", "charge_service_code", "charge_description", "amount"]


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
            "facility",
            "owner",
            "amount",
            "method",
            "reference",
            "note",
            "received_by",
            "received_by_name",
            "received_at",
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
