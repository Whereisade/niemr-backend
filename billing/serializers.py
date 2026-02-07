from decimal import Decimal

from django.db import models, transaction
from rest_framework import serializers

from .models import Service, Price, Charge, Payment, PaymentAllocation, HMOPrice
from .enums import ChargeStatus, PaymentMethod, PaymentSource
from .services.pricing import resolve_price
from django.utils import timezone

# Updated imports for new HMO models
from patients.models import SystemHMO, HMOTier, FacilityHMO, Patient


# ============================================================================
# CATALOG & PRICE SERIALIZERS
# ============================================================================

class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ["id", "code", "name", "default_price", "is_active"]


class PriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Price
        fields = ["id", "facility", "owner", "service", "amount", "currency"]


# ============================================================================
# CHARGE SERIALIZERS
# ============================================================================

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
        facility = getattr(user, "facility", None) if getattr(user, "facility_id", None) else None
        owner = None
        role = (getattr(user, "role", "") or "").upper()

        if not facility and role in {"ADMIN", "SUPER_ADMIN"} and getattr(patient, "facility_id", None):
            facility = patient.facility
        if not facility:
            owner = user

        # Get pricing with HMO support
        system_hmo = getattr(patient, 'system_hmo', None)
        hmo_tier = getattr(patient, 'hmo_tier', None)
        
        unit_price = resolve_price(
            service=validated["service"], 
            facility=facility, 
            owner=owner,
            system_hmo=system_hmo,
            tier=hmo_tier,
        )
        
        # If no price configured, use service default or 0
        if unit_price is None:
            unit_price = validated["service"].default_price or Decimal("0.00")
        
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
    patient_system_hmo = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()

    # ---------------------------------------------------------------------
    # Derived payer context
    # ---------------------------------------------------------------------
    # We do not currently store a "bill_to" field on the Charge model.
    # For now, we derive a payer context from the patient's insurance state
    # so the UI can avoid treating HMO-covered charges as *patient* debt.
    payment_source = serializers.SerializerMethodField()
    hmo_id = serializers.SerializerMethodField()
    hmo_name = serializers.SerializerMethodField()
    patient_portion = serializers.SerializerMethodField()
    hmo_portion = serializers.SerializerMethodField()
    claim_status = serializers.SerializerMethodField()

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
            "patient_system_hmo",
            "payment_source",
            "hmo_id",
            "hmo_name",
            "patient_portion",
            "hmo_portion",
            "claim_status",
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

    def _patient_is_insured(self, patient) -> bool:
        if not patient:
            return False
        # Prefer explicit insurance status, but also treat any attached HMO as insured.
        status = (getattr(patient, "insurance_status", "") or "").upper()
        if status == "INSURED":
            return True
        return bool(getattr(patient, "system_hmo_id", None) or getattr(patient, "hmo_id", None))

    def _patient_hmo_display(self, patient):
        """Return (hmo_id, hmo_name) for display.

        Prefer SystemHMO, fall back to legacy HMO.
        """
        if not patient:
            return (None, "")
        shmo = getattr(patient, "system_hmo", None)
        if shmo:
            return (shmo.id, shmo.name)
        legacy = getattr(patient, "hmo", None)
        if legacy:
            return (legacy.id, legacy.name)
        return (None, "")

    def get_patient_name(self, obj):
        p = getattr(obj, "patient", None)
        if not p:
            return ""
        name = f"{getattr(p, 'first_name', '')} {getattr(p, 'last_name', '')}".strip()
        return name or str(p)

    def get_patient_hmo(self, obj):
        """Return patient's legacy HMO info if they have one"""
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
    
    def get_patient_system_hmo(self, obj):
        """Return patient's System HMO info if they have one"""
        p = getattr(obj, "patient", None)
        if not p:
            return None
        system_hmo = getattr(p, "system_hmo", None)
        if not system_hmo:
            return None
        
        tier = getattr(p, "hmo_tier", None)
        return {
            "id": system_hmo.id,
            "name": system_hmo.name,
            "tier_id": tier.id if tier else None,
            "tier_name": tier.name if tier else None,
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

    def get_payment_source(self, obj):
        p = getattr(obj, "patient", None)
        return "HMO" if self._patient_is_insured(p) else "PATIENT_DIRECT"

    def get_hmo_id(self, obj):
        p = getattr(obj, "patient", None)
        hid, _ = self._patient_hmo_display(p)
        return hid

    def get_hmo_name(self, obj):
        p = getattr(obj, "patient", None)
        _, name = self._patient_hmo_display(p)
        return name

    def get_patient_portion(self, obj):
        amt = Decimal(str(getattr(obj, "amount", 0) or 0))
        return Decimal("0.00") if self.get_payment_source(obj) == "HMO" else amt

    def get_hmo_portion(self, obj):
        amt = Decimal(str(getattr(obj, "amount", 0) or 0))
        return amt if self.get_payment_source(obj) == "HMO" else Decimal("0.00")

    def get_claim_status(self, obj):
        # Placeholder: claim tracking is not yet modeled for charges.
        return None


# ============================================================================
# PAYMENT SERIALIZERS
# ============================================================================

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
            "system_hmo",
            "facility_hmo",
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
        """Return the HMO name for display.

        Prefer the new FacilityHMO/SystemHMO linkage, fall back to legacy patients.HMO.
        """
        fhmo = getattr(obj, 'facility_hmo', None)
        if fhmo and getattr(fhmo, 'system_hmo', None):
            return fhmo.system_hmo.name
        shmo = getattr(obj, 'system_hmo', None)
        if shmo:
            return shmo.name
        legacy = getattr(obj, 'hmo', None)
        if legacy:
            return legacy.name
        return ""

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


# ============================================================================
# HMO PAYMENT SERIALIZERS
# ============================================================================

class HMOPaymentCreateSerializer(serializers.Serializer):
    """
    Serializer for creating HMO bulk payments.

    **Recommended:** pass `hmo_id` as the FacilityHMO relationship id (frontend does this).

    Backward-compatible: `system_hmo_id` is accepted as a fallback, in which case the serializer
    will resolve the active FacilityHMO relationship for the current facility/provider.

    Supports allocating payment to multiple patient charges.
    """

    # HMO identification (recommended)
    hmo_id = serializers.IntegerField(required=False, help_text="FacilityHMO relationship ID")

    # Backward-compatible (optional)
    system_hmo_id = serializers.IntegerField(required=False, help_text="SystemHMO ID (fallback)")

    # Payment details
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.TRANSFER)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True)
    note = serializers.CharField(required=False, allow_blank=True)

    # Billing period (optional)
    period_start = serializers.DateField(required=False, allow_null=True)
    period_end = serializers.DateField(required=False, allow_null=True)

    # Allocations (optional - for specific charge allocation)
    allocations = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list,
        help_text="Optional list of {charge_id, amount} for specific allocation",
    )

    # Auto-allocate to oldest unpaid charges if no specific allocations
    auto_allocate = serializers.BooleanField(default=True)

    def validate(self, attrs):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        facility = getattr(user, 'facility', None)

        # Resolve FacilityHMO + SystemHMO
        facility_hmo = None
        system_hmo = None

        hmo_id = attrs.get('hmo_id')
        system_hmo_id = attrs.get('system_hmo_id')

        if hmo_id:
            try:
                facility_hmo = FacilityHMO.objects.select_related('system_hmo').get(id=hmo_id, is_active=True)
            except FacilityHMO.DoesNotExist:
                raise serializers.ValidationError({'hmo_id': 'Facility HMO relationship not found or inactive'})

            # Scope check
            if facility:
                if facility_hmo.facility_id != facility.id:
                    raise serializers.ValidationError({'hmo_id': 'This HMO relationship does not belong to your facility'})
            else:
                if facility_hmo.owner_id != user.id:
                    raise serializers.ValidationError({'hmo_id': 'This HMO relationship does not belong to your practice'})

            system_hmo = facility_hmo.system_hmo
        else:
            # Fallback: resolve by SystemHMO id + active relationship
            if not system_hmo_id:
                raise serializers.ValidationError({'hmo_id': 'hmo_id is required'})
            try:
                system_hmo = SystemHMO.objects.get(id=system_hmo_id, is_active=True)
            except SystemHMO.DoesNotExist:
                raise serializers.ValidationError({'system_hmo_id': 'HMO not found or inactive'})

            rel_filter = {'system_hmo': system_hmo, 'is_active': True}
            if facility:
                rel_filter['facility'] = facility
            else:
                rel_filter['owner'] = user

            facility_hmo = FacilityHMO.objects.filter(**rel_filter).select_related('system_hmo').first()
            if not facility_hmo:
                raise serializers.ValidationError({'system_hmo_id': f'Your {"facility" if facility else "practice"} does not have an active relationship with this HMO'})

        # Period sanity
        ps = attrs.get('period_start')
        pe = attrs.get('period_end')
        if ps and pe and ps > pe:
            raise serializers.ValidationError({'period_end': 'period_end must be on/after period_start'})

        attrs['facility_hmo'] = facility_hmo
        attrs['system_hmo'] = system_hmo
        attrs['facility'] = facility
        attrs['owner'] = user if not facility else None
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        """Create the HMO payment record and allocate to charges."""
        from .services.rollup import recompute_charge_status

        request = self.context.get('request')
        user = request.user

        facility = validated_data.get('facility')
        owner = validated_data.get('owner')
        facility_hmo = validated_data['facility_hmo']
        system_hmo = validated_data['system_hmo']

        amount = validated_data['amount']
        allocations_data = validated_data.get('allocations', [])
        auto_allocate = validated_data.get('auto_allocate', True)

        payment = Payment.objects.create(
            patient=None,  # bulk
            hmo=None,      # legacy
            system_hmo=system_hmo,
            facility_hmo=facility_hmo,
            facility=facility,
            owner=owner,
            payment_source=PaymentSource.HMO,
            amount=amount,
            method=validated_data.get('method', PaymentMethod.TRANSFER),
            reference=validated_data.get('reference', ''),
            note=validated_data.get('note', f'HMO Payment from {system_hmo.name}'),
            received_by=user,
            period_start=validated_data.get('period_start'),
            period_end=validated_data.get('period_end'),
        )

        # Base charge filter for this scope
        base_filter = {
            'patient__system_hmo': system_hmo,
        }
        if facility:
            base_filter['facility'] = facility
            base_filter['owner__isnull'] = True
        else:
            base_filter['owner'] = owner
            base_filter['facility__isnull'] = True

        if allocations_data:
            # Manual allocation
            for item in allocations_data:
                try:
                    charge_id = int(item.get('charge_id'))
                    alloc_amount = Decimal(str(item.get('amount')))
                except (TypeError, ValueError, KeyError):
                    continue

                if alloc_amount <= 0:
                    continue

                try:
                    charge = Charge.objects.select_for_update().get(id=charge_id, **base_filter)
                except Charge.DoesNotExist:
                    continue

                already = charge.allocations.aggregate(t=models.Sum('amount'))['t'] or Decimal('0.00')
                due = Decimal(str(charge.amount)) - Decimal(str(already))
                take = min(alloc_amount, max(due, Decimal('0.00')))

                if take <= 0:
                    continue

                PaymentAllocation.objects.create(payment=payment, charge=charge, amount=take)
                recompute_charge_status(charge)

        elif auto_allocate:
            # Auto allocate to oldest unpaid
            remaining = Decimal(str(amount))

            charges = (
                Charge.objects.select_for_update()
                .filter(**base_filter)
                .exclude(status=ChargeStatus.VOID)
                .exclude(status=ChargeStatus.PAID)
                .order_by('created_at', 'id')
            )

            for charge in charges:
                if remaining <= 0:
                    break

                already = charge.allocations.aggregate(t=models.Sum('amount'))['t'] or Decimal('0.00')
                due = Decimal(str(charge.amount)) - Decimal(str(already))
                if due <= 0:
                    recompute_charge_status(charge)
                    continue

                take = min(remaining, due)
                PaymentAllocation.objects.create(payment=payment, charge=charge, amount=take)
                remaining -= take
                recompute_charge_status(charge)

        return payment


class HMOOutstandingChargesSerializer(serializers.Serializer):
    """Summary of outstanding charges for an HMO"""
    total_charges = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_paid = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_outstanding = serializers.DecimalField(max_digits=12, decimal_places=2)
    patient_count = serializers.IntegerField()
    charge_count = serializers.IntegerField()


class HMOChargeDetailSerializer(serializers.Serializer):
    """Detailed charge info for HMO billing"""
    id = serializers.IntegerField()
    patient_id = serializers.IntegerField()
    patient_name = serializers.CharField()
    patient_tier = serializers.CharField(allow_null=True)
    service_code = serializers.CharField()
    service_name = serializers.CharField()
    description = serializers.CharField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    allocated = serializers.DecimalField(max_digits=12, decimal_places=2)
    outstanding = serializers.DecimalField(max_digits=12, decimal_places=2)
    status = serializers.CharField()
    created_at = serializers.DateTimeField()


# ============================================================================
# HMO PRICE SERIALIZERS
# ============================================================================

class HMOPriceSerializer(serializers.ModelSerializer):
    """Serializer for HMO-specific pricing."""
    
    system_hmo_name = serializers.CharField(source='system_hmo.name', read_only=True)
    tier_name = serializers.CharField(source='tier.name', read_only=True, allow_null=True)
    service_code = serializers.CharField(source='service.code', read_only=True)
    service_name = serializers.CharField(source='service.name', read_only=True)
    
    class Meta:
        model = HMOPrice
        fields = [
            'id',
            'facility',
            'owner',
            'system_hmo',
            'system_hmo_name',
            'tier',
            'tier_name',
            'service',
            'service_code',
            'service_name',
            'amount',
            'currency',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class HMOPriceCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating HMO prices."""
    
    class Meta:
        model = HMOPrice
        fields = [
            'system_hmo',
            'tier',
            'service',
            'amount',
            'currency',
            'is_active',
        ]
    
    def validate(self, attrs):
        """Validate tier belongs to the HMO."""
        system_hmo = attrs.get('system_hmo')
        tier = attrs.get('tier')
        
        if tier and system_hmo and tier.system_hmo_id != system_hmo.id:
            raise serializers.ValidationError({
                'tier': 'Tier does not belong to this HMO'
            })
        
        return attrs