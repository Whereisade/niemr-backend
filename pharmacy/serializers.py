from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import models, transaction
from rest_framework import serializers

from accounts.enums import UserRole
from billing.models import Service, Charge
from billing.services.pricing import resolve_price
from notifications.services.notify import notify_user, notify_patient
from notifications.enums import Topic, Priority

from .models import Drug, StockItem, StockTxn, Prescription, PrescriptionItem, DispenseEvent
from .enums import RxStatus, TxnType


User = get_user_model()


# --- Catalog ---
class DrugSerializer(serializers.ModelSerializer):
    class Meta:
        model = Drug
        fields = [
            "id",
            "code",
            "name",
            "strength",
            "form",
            "route",
            "qty_per_unit",
            "unit_price",
            "is_active",
            "facility",
            "created_by",
        ]
        read_only_fields = ["facility", "created_by"]

    def validate_code(self, value):
        """
        Validate that the code is unique within the user's scope (facility or user).
        """
        request = self.context.get("request")
        if not request or not request.user:
            return value

        u = request.user
        code = (value or "").strip().upper()

        if getattr(u, "facility_id", None):
            # Facility staff: check uniqueness within facility
            exists = Drug.objects.filter(
                code__iexact=code,
                facility_id=u.facility_id,
                is_active=True,
            ).exists()
            if exists:
                raise serializers.ValidationError(
                    f"A drug with code '{code}' already exists in your facility catalog."
                )
        else:
            # Independent pharmacy: check uniqueness within user's drugs
            exists = Drug.objects.filter(
                code__iexact=code,
                facility__isnull=True,
                created_by_id=u.id,
                is_active=True,
            ).exists()
            if exists:
                raise serializers.ValidationError(
                    f"A drug with code '{code}' already exists in your catalog."
                )

        return code


# --- Stock ---
class StockItemSerializer(serializers.ModelSerializer):
    drug = DrugSerializer(read_only=True)
    drug_id = serializers.PrimaryKeyRelatedField(
        source="drug",
        queryset=Drug.objects.filter(is_active=True),
        write_only=True,
    )

    class Meta:
        model = StockItem
        fields = ["id", "facility", "owner", "drug", "drug_id", "current_qty"]
        read_only_fields = ["facility", "owner"]


class StockTxnSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockTxn
        fields = [
            "id",
            "facility",
            "owner",
            "drug",
            "txn_type",
            "qty",
            "note",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["facility", "owner", "created_by", "created_at"]


# --- Prescriptions ---
class PrescriptionItemWriteSerializer(serializers.ModelSerializer):
    """
    Supports either:
    - drug_code (catalog)
    - drug_name (free-text)
    """
    drug_code = serializers.CharField(write_only=True, required=False, allow_blank=True)
    drug_name = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = PrescriptionItem
        fields = [
            "id",
            "drug_code",
            "drug_name",
            "dose",
            "frequency",
            "duration_days",
            "qty_prescribed",
            "instruction",
        ]

    def validate(self, attrs):
        code = (attrs.pop("drug_code", "") or "").strip()
        name = (attrs.get("drug_name", "") or "").strip()

        if code:
            request = self.context.get("request")
            u = request.user if request else None

            drug = None
            if u and getattr(u, "facility_id", None):
                drug = Drug.objects.filter(
                    code__iexact=code,
                    facility_id=u.facility_id,
                    is_active=True,
                ).first()
            elif u:
                drug = Drug.objects.filter(
                    code__iexact=code,
                    created_by_id=u.id,
                    is_active=True,
                ).first()

            # Fallback to global/legacy drugs
            if not drug:
                drug = Drug.objects.filter(
                    code__iexact=code,
                    facility__isnull=True,
                    created_by__isnull=True,
                    is_active=True,
                ).first()

            if not drug:
                raise serializers.ValidationError(
                    {"drug_code": f"Unknown/inactive drug_code: {code}"}
                )

            attrs["drug"] = drug
            attrs["drug_name"] = ""
            return attrs

        if not name:
            raise serializers.ValidationError(
                {"detail": "Provide either drug_code (catalog) or drug_name (free-text)."}
            )

        attrs["drug"] = None
        attrs["drug_name"] = name
        return attrs


class PrescriptionItemReadSerializer(serializers.ModelSerializer):
    drug = DrugSerializer(allow_null=True)
    remaining = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = PrescriptionItem
        fields = [
            "id",
            "drug",
            "drug_name",
            "display_name",
            "dose",
            "frequency",
            "duration_days",
            "qty_prescribed",
            "qty_dispensed",
            "remaining",
            "instruction",
        ]

    def get_remaining(self, obj):
        return obj.remaining()

    def get_display_name(self, obj):
        return obj.display_name


class PrescriptionCreateSerializer(serializers.ModelSerializer):
    items = PrescriptionItemWriteSerializer(many=True)
    outsourced_to = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = Prescription
        fields = ["id", "patient", "encounter_id", "note", "outsourced_to", "items"]

    def validate_outsourced_to(self, value):
        if value in (None, "", 0):
            return None

        user = User.objects.filter(id=value).first()
        if not user:
            raise serializers.ValidationError("Invalid outsourced_to user id")

        role = (getattr(user, "role", "") or "").upper()
        if role != UserRole.PHARMACY:
            raise serializers.ValidationError("outsourced_to must be a PHARMACY user")

        # Outsourcing targets independent pharmacies (facility=None)
        if getattr(user, "facility_id", None):
            raise serializers.ValidationError(
                "outsourced_to must be an independent PHARMACY (facility must be null)"
            )

        return user.id

    @transaction.atomic
    def create(self, validated):
        u = self.context["request"].user
        items = validated.pop("items", [])
        outsourced_to_id = validated.pop("outsourced_to", None)

        patient = validated["patient"]
        facility = u.facility if getattr(u, "facility_id", None) else getattr(patient, "facility", None)

        rx = Prescription.objects.create(
            patient=patient,
            facility=facility,
            prescribed_by=u,
            outsourced_to_id=outsourced_to_id,
            **validated,
        )

        for it in items:
            PrescriptionItem.objects.create(prescription=rx, **it)

        # Notify outsourced pharmacy if applicable
        if outsourced_to_id:
            try:
                notify_user(
                    user_id=outsourced_to_id,
                    topic=Topic.PRESCRIPTION_READY,
                    priority=Priority.NORMAL,
                    title="New outsourced prescription",
                    body=f"Prescription #{rx.id} assigned to you.",
                    data={"prescription_id": rx.id},
                    facility_id=getattr(facility, "id", None),
                    action_url="/provider/pharmacy",
                    group_key=f"RX:{rx.id}:ASSIGNED",
                )
            except Exception:
                pass

        return rx


class PrescriptionReadSerializer(serializers.ModelSerializer):
    items = PrescriptionItemReadSerializer(many=True)
    outsourced_to_name = serializers.SerializerMethodField()

    class Meta:
        model = Prescription
        fields = [
            "id",
            "patient",
            "facility",
            "prescribed_by",
            "encounter_id",
            "status",
            "note",
            "outsourced_to",
            "outsourced_to_name",
            "created_at",
            "items",
        ]

    def get_outsourced_to_name(self, obj):
        u = getattr(obj, "outsourced_to", None)
        if not u:
            return None
        full = (u.get_full_name() or "").strip()
        return full or u.email


class DispenseSerializer(serializers.Serializer):
    """
    Dispense a quantity for a specific item.
    - For outsourced prescriptions OR free-text meds: bypass facility stock checks.
    - For facility catalog meds (not outsourced): enforce facility stock + create StockTxn.
    Also creates billing Charge when facility + catalog drug are available.
    """
    item_id = serializers.IntegerField()
    qty = serializers.IntegerField(min_value=1)
    note = serializers.CharField(required=False, allow_blank=True)

    def save(self, *, rx: Prescription, user):
        item = rx.items.filter(id=self.validated_data["item_id"]).first()
        if not item:
            raise serializers.ValidationError("Item not found in prescription")

        take = int(self.validated_data["qty"])
        if take <= 0:
            raise serializers.ValidationError("qty must be >= 1")

        if item.remaining() <= 0:
            raise serializers.ValidationError("Item already fully dispensed")

        take = min(take, item.remaining())

        is_outsourced = bool(rx.outsourced_to_id)
        is_free_text = not bool(item.drug_id)

        # Determine stock scope:
        # - Facility pharmacy (not outsourced): facility stock
        # - Independent outsourced pharmacy: owner stock (pharmacy user)
        stock_scope = None
        role = (getattr(user, "role", "") or "").upper()

        if not is_free_text:
            if (not is_outsourced) and bool(rx.facility_id) and bool(getattr(user, "facility_id", None)) and rx.facility_id == user.facility_id:
                stock_scope = {"facility": rx.facility}
            elif is_outsourced and role == UserRole.PHARMACY and (not getattr(user, "facility_id", None)) and rx.outsourced_to_id == getattr(user, "id", None):
                stock_scope = {"owner": user}

        stock_required = bool(stock_scope)

        with transaction.atomic():
            if stock_required:
                if "facility" in stock_scope:
                    stock, _ = StockItem.objects.select_for_update().get_or_create(
                        facility=stock_scope["facility"],
                        owner=None,
                        drug=item.drug,
                        defaults={"current_qty": 0},
                    )
                else:
                    stock, _ = StockItem.objects.select_for_update().get_or_create(
                        facility=None,
                        owner=stock_scope["owner"],
                        drug=item.drug,
                        defaults={"current_qty": 0},
                    )

                if stock.current_qty < take:
                    raise serializers.ValidationError(
                        f"Insufficient stock for {item.drug.name}. In stock: {stock.current_qty}"
                    )

                stock.current_qty -= take
                stock.save(update_fields=["current_qty"])

                StockTxn.objects.create(
                    facility=stock_scope.get("facility"),
                    owner=stock_scope.get("owner"),
                    drug=item.drug,
                    txn_type=TxnType.OUT,
                    qty=-take,
                    note=f"Dispense Rx#{rx.id}",
                    created_by=user,
                )

            # record dispense event
            DispenseEvent.objects.create(
                prescription_item=item,
                qty=take,
                dispensed_by=user,
                note=self.validated_data.get("note", ""),
            )

            # update item qty
            item.qty_dispensed += take
            item.save(update_fields=["qty_dispensed"])

            # roll up rx status
            totals = rx.items.aggregate(
                total=models.Sum("qty_prescribed"),
                out=models.Sum("qty_dispensed"),
            )
            total = totals["total"] or 0
            out = totals["out"] or 0

            if out == 0:
                rx.status = RxStatus.PRESCRIBED
            elif out < total:
                rx.status = RxStatus.PARTIALLY_DISPENSED
            else:
                rx.status = RxStatus.DISPENSED
            rx.save(update_fields=["status"])

            # billing only when facility exists AND catalog drug exists
            facility = rx.facility or getattr(rx.patient, "facility", None)
            if facility and rx.patient and item.drug_id:
                drug = item.drug
                service_code = f"DRUG:{drug.code}"

                service_defaults = {
                    "name": f"{drug.name} {drug.strength}".strip(),
                    "default_price": drug.unit_price,
                    "is_active": True,
                }
                service, _ = Service.objects.get_or_create(code=service_code, defaults=service_defaults)

                unit_price = resolve_price(facility=facility, service=service)
                amount = (unit_price or Decimal("0")) * Decimal(take)

                charge = Charge.objects.create(
                    patient=rx.patient,
                    facility=facility,
                    service=service,
                    description=f"{drug.name} x{take}",
                    unit_price=unit_price,
                    qty=take,
                    amount=amount,
                    prescription_id=rx.id,
                    encounter_id=rx.encounter_id,
                    created_by=user,
                )
                try:
                    if rx.patient:
                        notify_patient(
                            patient=rx.patient,
                            topic=Topic.PRESCRIPTION_READY,
                            priority=Priority.NORMAL,
                            title="Medication dispensed",
                            body=f"{drug.name} x{take} has been dispensed.",
                            data={
                                "prescription_id": rx.id,
                                "charge_id": charge.id,
                                "drug_id": drug.id,
                                "qty": take,
                            },
                            facility_id=facility.id,
                            action_url="/patient/pharmacy",
                            group_key=f"RX:{rx.id}:DISPENSE",
                        )
                except Exception:
                    pass

        return item


