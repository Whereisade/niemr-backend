from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import models, transaction
from rest_framework import serializers

from accounts.enums import UserRole
from billing.models import Service, Charge
from billing.services.pricing import resolve_price
from notifications.services.notify import notify_user
from notifications.enums import Topic

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
                is_active=True
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
                is_active=True
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
        fields = ["id", "facility", "drug", "drug_id", "current_qty"]
        read_only_fields = ["facility"]


class StockTxnSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockTxn
        fields = [
            "id",
            "facility",
            "drug",
            "txn_type",
            "qty",
            "note",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["facility", "created_by", "created_at"]


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
            # Look up drug in the prescriber's facility catalog or their own catalog
            request = self.context.get("request")
            u = request.user if request else None
            
            drug = None
            if u and getattr(u, "facility_id", None):
                # Facility staff: look in facility's catalog
                drug = Drug.objects.filter(
                    code__iexact=code,
                    facility_id=u.facility_id,
                    is_active=True
                ).first()
            elif u:
                # Independent provider: look in their own catalog
                drug = Drug.objects.filter(
                    code__iexact=code,
                    created_by_id=u.id,
                    is_active=True
                ).first()
            
            # Fallback to legacy global drugs (facility=None, created_by=None)
            if not drug:
                drug = Drug.objects.filter(
                    code__iexact=code,
                    facility__isnull=True,
                    created_by__isnull=True,
                    is_active=True
                ).first()
            
            if not drug:
                raise serializers.ValidationError({"drug_code": f"Unknown/inactive drug_code: {code}"})
            
            attrs["drug"] = drug
            attrs["drug_name"] = ""
            return attrs

        if not name:
            raise serializers.ValidationError({"detail": "Provide either drug_code (catalog) or drug_name (free-text)."})

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

        # Outsourcing targets independent providers (facility=None)
        if getattr(user, "facility_id", None):
            raise serializers.ValidationError("outsourced_to must be an independent PHARMACY (facility must be null)")

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
            encounter_id=validated.get("encounter_id"),
            status=RxStatus.PRESCRIBED,
            note=validated.get("note", ""),
            outsourced_to_id=outsourced_to_id,
        )

        for i in items:
            PrescriptionItem.objects.create(prescription=rx, **i)

        # Link to encounter (append rx.id to encounter.prescription_ids) — best-effort
        if rx.encounter_id:
            try:
                from encounters.models import Encounter

                enc = Encounter.objects.filter(id=rx.encounter_id).first()
                if enc:
                    ids = list(enc.prescription_ids or [])
                    if rx.id not in ids:
                        ids.append(rx.id)
                        enc.prescription_ids = ids
                        enc.save(update_fields=["prescription_ids", "updated_at"])
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

        remaining = item.remaining()
        take = self.validated_data["qty"]
        if take > remaining:
            raise serializers.ValidationError(f"Only {remaining} remaining to dispense")

        is_outsourced = bool(getattr(rx, "outsourced_to_id", None))
        is_free_text = item.drug_id is None

        # stock applies only when:
        # - not outsourced
        # - has facility
        # - item is catalog drug
        stock_required = (not is_outsourced) and bool(rx.facility_id) and (not is_free_text)

        with transaction.atomic():
            if stock_required:
                stock, _ = StockItem.objects.select_for_update().get_or_create(
                    facility=rx.facility,
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
                    facility=rx.facility,
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
                    if rx.patient.user_id:
                        notify_user(
                            user=rx.patient.user,
                            topic=Topic.BILL_CHARGE_ADDED,
                            title="Medication dispensed",
                            body=f"{drug.name} x{take} - {amount}",
                            data={"charge_id": charge.id, "prescription_id": rx.id},
                            facility_id=facility.id,
                        )
                except Exception:
                    pass

        return item