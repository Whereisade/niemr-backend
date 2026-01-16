from decimal import Decimal

from django.db import models, transaction
from rest_framework import serializers

from .models import Service, Price, Charge, Payment, PaymentAllocation, HMOPrice
from .enums import ChargeStatus, PaymentMethod, PaymentSource
from .services.pricing import resolve_price
from django.utils import timezone

# Updated imports for new HMO models
from patients.models import SystemHMO, HMOTier, FacilityHMO, Patient

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
    Serializer for creating HMO payments.
    
    Updated to work with SystemHMO architecture:
    - Validates that the facility/provider has a relationship with the SystemHMO
    - Supports tier-specific pricing
    - Works for both facility and independent provider contexts
    """
    
    # Patient and HMO identification
    patient_id = serializers.IntegerField(required=True)
    system_hmo_id = serializers.IntegerField(required=True)
    tier_id = serializers.IntegerField(required=False, allow_null=True)
    
    # Payment details
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    payment_date = serializers.DateField(required=False)
    reference_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    
    # Items being paid for (optional - for itemized payments)
    charge_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        default=list
    )
    
    def validate(self, attrs):
        """
        Validate the HMO payment request.
        
        Checks:
        1. Patient exists and belongs to the facility/provider
        2. SystemHMO exists and is active
        3. Facility/provider has an active relationship with the HMO
        4. If tier specified, it belongs to the SystemHMO
        5. Patient is enrolled with this HMO (optional - depends on business rules)
        """
        request = self.context.get('request')
        facility = self.context.get('facility')
        owner = self.context.get('owner')  # For independent providers
        
        patient_id = attrs.get('patient_id')
        system_hmo_id = attrs.get('system_hmo_id')
        tier_id = attrs.get('tier_id')
        
        # 1. Validate patient exists and is accessible
        try:
            if facility:
                patient = Patient.objects.get(id=patient_id, facility=facility)
            elif owner:
                patient = Patient.objects.get(id=patient_id, owner=owner)
            else:
                raise serializers.ValidationError({
                    'detail': 'No facility or provider context available'
                })
        except Patient.DoesNotExist:
            raise serializers.ValidationError({
                'patient_id': 'Patient not found or not accessible'
            })
        
        attrs['patient'] = patient
        
        # 2. Validate SystemHMO exists and is active
        try:
            system_hmo = SystemHMO.objects.get(id=system_hmo_id, is_active=True)
        except SystemHMO.DoesNotExist:
            raise serializers.ValidationError({
                'system_hmo_id': 'HMO not found or is inactive'
            })
        
        attrs['system_hmo'] = system_hmo
        
        # 3. Validate facility/provider has relationship with HMO
        facility_hmo_filter = {
            'system_hmo': system_hmo,
            'is_active': True
        }
        
        if facility:
            facility_hmo_filter['facility'] = facility
        elif owner:
            facility_hmo_filter['owner'] = owner
        
        try:
            facility_hmo = FacilityHMO.objects.get(**facility_hmo_filter)
        except FacilityHMO.DoesNotExist:
            raise serializers.ValidationError({
                'system_hmo_id': f'Your {"facility" if facility else "practice"} does not have an active relationship with this HMO'
            })
        
        attrs['facility_hmo'] = facility_hmo
        
        # 4. Validate tier if provided
        if tier_id:
            try:
                tier = HMOTier.objects.get(
                    id=tier_id,
                    system_hmo=system_hmo,
                    is_active=True
                )
            except HMOTier.DoesNotExist:
                raise serializers.ValidationError({
                    'tier_id': 'Tier not found or does not belong to this HMO'
                })
            
            attrs['tier'] = tier
        else:
            attrs['tier'] = None
        
        # 5. Optional: Validate patient is enrolled with this HMO
        # Uncomment if you want to enforce patient HMO enrollment
        # if patient.system_hmo_id != system_hmo_id:
        #     raise serializers.ValidationError({
        #         'patient_id': 'Patient is not enrolled with this HMO'
        #     })
        
        return attrs
    
    @transaction.atomic
    def create(self, validated_data):
        """
        Create the HMO payment record.
        
        This should be integrated with your existing Payment model.
        Adjust according to your actual Payment model structure.
        """
        from billing.models import Payment, Charge  # Import here to avoid circular imports
        
        request = self.context.get('request')
        facility = self.context.get('facility')
        owner = self.context.get('owner')
        
        patient = validated_data['patient']
        system_hmo = validated_data['system_hmo']
        tier = validated_data.get('tier')
        amount = validated_data['amount']
        charge_ids = validated_data.get('charge_ids', [])
        
        # Create the payment record
        payment_data = {
            'patient': patient,
            'amount': amount,
            'payment_type': 'HMO',
            'payment_date': validated_data.get('payment_date', timezone.now().date()),
            'reference_number': validated_data.get('reference_number', ''),
            'notes': validated_data.get('notes', ''),
            'recorded_by': request.user if request else None,
            # Store HMO information
            'system_hmo': system_hmo,
            'hmo_tier': tier,
        }
        
        if facility:
            payment_data['facility'] = facility
        elif owner:
            payment_data['owner'] = owner
        
        payment = Payment.objects.create(**payment_data)
        
        # Link charges if provided
        if charge_ids:
            charges = Charge.objects.filter(
                id__in=charge_ids,
                patient=patient,
                status__in=['PENDING', 'PARTIALLY_PAID']
            )
            
            # Apply payment to charges
            remaining_amount = amount
            for charge in charges:
                if remaining_amount <= 0:
                    break
                    
                charge_balance = charge.balance
                payment_amount = min(remaining_amount, charge_balance)
                
                charge.apply_payment(payment_amount, payment)
                remaining_amount -= payment_amount
        
        return payment



class HMOOutstandingChargesSerializer(serializers.Serializer):
    """Summary of outstanding charges for an HMO"""
    total_charges = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_paid = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_outstanding = serializers.DecimalField(max_digits=12, decimal_places=2)
    patient_count = serializers.IntegerField()
    charge_count = serializers.IntegerField()