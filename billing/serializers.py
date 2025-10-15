from decimal import Decimal
from django.db import transaction
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
        fields = ["id","facility","service","amount","currency"]

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
        facility = user.facility or patient.facility
        unit_price = resolve_price(facility=facility, service=validated["service"])
        qty = validated.get("qty") or 1
        amount = unit_price * qty
        return Charge.objects.create(
            patient=patient, facility=facility, service=validated["service"],
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
        fields = ["id","patient","facility","service","description","unit_price","qty","amount","status",
                  "encounter_id","lab_order_id","imaging_request_id","prescription_id",
                  "created_by","created_at"]

# --- Payments ---
class PaymentCreateSerializer(serializers.ModelSerializer):
    allocations = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField()),
        write_only=True,
        help_text="List of {charge_id, amount}"
    )

    class Meta:
        model = Payment
        fields = ["id","patient","amount","method","reference","note","allocations"]

    @transaction.atomic
    def create(self, validated):
        from .services.rollup import recompute_charge_status
        user = self.context["request"].user
        patient = validated["patient"]
        payment = Payment.objects.create(
            patient=patient,
            facility=user.facility or patient.facility,
            amount=validated["amount"],
            method=validated.get("method") or PaymentMethod.CASH,
            reference=validated.get("reference",""),
            note=validated.get("note",""),
            received_by=user,
        )
        total_alloc = Decimal("0.00")
        for item in self.validated_data["allocations"]:
            cid = int(item["charge_id"])
            amt = Decimal(str(item["amount"]))
            charge = Charge.objects.select_for_update().get(id=cid, patient=patient)
            PaymentAllocation.objects.create(payment=payment, charge=charge, amount=amt)
            total_alloc += amt
            recompute_charge_status(charge)
        # (Optional) validate total_alloc == payment.amount; we allow under/over then reconcile later.
        return payment

class PaymentReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id","patient","facility","amount","method","reference","note","received_by","received_at"]
