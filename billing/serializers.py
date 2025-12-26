from decimal import Decimal
from django.db import models, transaction
from rest_framework import serializers
from .models import Service, Price, Charge, Payment, PaymentAllocation
from .enums import ChargeStatus, PaymentMethod
from .services.pricing import resolve_price
from facilities.models import Facility

# --- Catalog & Price ---
class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ["id","code","name","default_price","is_active"]

class PriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Price
        fields = ["id","facility","owner","service","amount","currency"]

# --- Charges ---
class ChargeCreateSerializer(serializers.ModelSerializer):
    service_code = serializers.CharField(write_only=True)

    class Meta:
        model = Charge
        fields = ["id","patient","service_code","description","qty","encounter_id","lab_order_id","imaging_request_id","prescription_id"]

    def validate(self, attrs):
        from .models import Service
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
            patient=patient, facility=facility, owner=owner, service=validated["service"],
            description=validated.get("description",""),
            unit_price=unit_price, qty=qty, amount=amount,
            created_by=user,
            encounter_id=validated.get("encounter_id"),
            lab_order_id=validated.get("lab_order_id"),
            imaging_request_id=validated.get("imaging_request_id"),
            prescription_id=validated.get("prescription_id"),
        )

class ChargeReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Charge
        fields = ["id","patient","facility","owner","service","description","unit_price","qty","amount","status",
                  "encounter_id","lab_order_id","imaging_request_id","prescription_id",
                  "created_by","created_at"]

# --- Payments ---
class PaymentCreateSerializer(serializers.ModelSerializer):
    allocations = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField()),
        write_only=True,
        required=False,
        help_text="Optional list of {charge_id, amount}. If omitted, we'll auto-allocate to oldest unpaid charges."
    )

    class Meta:
        model = Payment
        fields = ["id","patient","amount","method","reference","note","allocations"]

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
            reference=validated.get("reference",""),
            note=validated.get("note",""),
            received_by=user,
        )
        total_alloc = Decimal("0.00")

        allocations = self.validated_data.get("allocations")
        # Explicit allocations
        if allocations:
            for item in allocations:
                cid = int(item["charge_id"])
                amt = Decimal(str(item["amount"]))
                if amt <= 0:
                    continue
                charge = Charge.objects.select_for_update().get(
                    id=cid,
                    patient=patient,
                    facility=facility,
                    owner=owner,
                )
                PaymentAllocation.objects.create(payment=payment, charge=charge, amount=amt)
                total_alloc += amt
                recompute_charge_status(charge)
            return payment

        # Auto-allocation: oldest unpaid charges in this scope
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
            PaymentAllocation.objects.create(payment=payment, charge=ch, amount=take)
            remaining -= take
            total_alloc += take
            recompute_charge_status(ch)

        # (Optional) validate total_alloc == payment.amount; we allow under/over then reconcile later.
        return payment

class PaymentReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id","patient","facility","owner","amount","method","reference","note","received_by","received_at"]
