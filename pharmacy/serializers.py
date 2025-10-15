from django.db import transaction
from rest_framework import serializers
from .models import Drug, StockItem, StockTxn, Prescription, PrescriptionItem, DispenseEvent
from .enums import RxStatus, TxnType

# --- Catalog ---
class DrugSerializer(serializers.ModelSerializer):
    class Meta:
        model = Drug
        fields = ["id","code","name","strength","form","route","qty_per_unit","unit_price","is_active"]

# --- Stock ---
class StockItemSerializer(serializers.ModelSerializer):
    drug = DrugSerializer(read_only=True)
    drug_id = serializers.PrimaryKeyRelatedField(source="drug", queryset=Drug.objects.filter(is_active=True), write_only=True)
    class Meta:
        model = StockItem
        fields = ["id","facility","drug","drug_id","current_qty"]
        read_only_fields = ["facility"]

class StockTxnSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockTxn
        fields = ["id","facility","drug","txn_type","qty","note","created_by","created_at"]
        read_only_fields = ["facility","created_by","created_at"]

# --- Prescriptions ---
class PrescriptionItemWriteSerializer(serializers.ModelSerializer):
    drug_code = serializers.CharField(write_only=True)
    class Meta:
        model = PrescriptionItem
        fields = ["id","drug_code","dose","frequency","duration_days","qty_prescribed","instruction"]

    def validate(self, attrs):
        code = attrs.pop("drug_code")
        try:
            drug = Drug.objects.get(code=code, is_active=True)
        except Drug.DoesNotExist:
            raise serializers.ValidationError(f"Unknown/inactive drug_code: {code}")
        attrs["drug"] = drug
        return attrs

class PrescriptionItemReadSerializer(serializers.ModelSerializer):
    drug = DrugSerializer()
    remaining = serializers.SerializerMethodField()
    class Meta:
        model = PrescriptionItem
        fields = ["id","drug","dose","frequency","duration_days","qty_prescribed","qty_dispensed","remaining","instruction"]
    def get_remaining(self, obj): return obj.remaining()

class PrescriptionCreateSerializer(serializers.ModelSerializer):
    items = PrescriptionItemWriteSerializer(many=True)
    class Meta:
        model = Prescription
        fields = ["id","patient","encounter_id","note","items"]

    @transaction.atomic
    def create(self, validated):
        u = self.context["request"].user
        rx = Prescription.objects.create(
            patient=validated["patient"],
            facility=u.facility if u.facility_id else validated["patient"].facility,
            prescribed_by=u,
            encounter_id=validated.get("encounter_id"),
            status=RxStatus.PRESCRIBED,
            note=validated.get("note",""),
        )
        for i in validated["items"]:
            PrescriptionItem.objects.create(prescription=rx, **i)
        return rx

class PrescriptionReadSerializer(serializers.ModelSerializer):
    items = PrescriptionItemReadSerializer(many=True)
    class Meta:
        model = Prescription
        fields = ["id","patient","facility","prescribed_by","encounter_id","status","note","created_at","items"]

class DispenseSerializer(serializers.Serializer):
    """
    Dispense a quantity for a specific item.
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

        # stock check & move
        with transaction.atomic():
            # stock row per facility+drug
            stock, _ = StockItem.objects.select_for_update().get_or_create(
                facility=rx.facility, drug=item.drug, defaults={"current_qty": 0}
            )
            if stock.current_qty < take:
                raise serializers.ValidationError(f"Insufficient stock for {item.drug.name}. In stock: {stock.current_qty}")

            stock.current_qty -= take
            stock.save(update_fields=["current_qty"])
            StockTxn.objects.create(
                facility=rx.facility, drug=item.drug,
                txn_type=TxnType.OUT, qty=-take, note=f"Dispense Rx#{rx.id}",
                created_by=user
            )
            # record dispense
            DispenseEvent.objects.create(
                prescription_item=item, qty=take, dispensed_by=user,
                note=self.validated_data.get("note","")
            )
            item.qty_dispensed += take
            item.save(update_fields=["qty_dispensed"])

            # rx status roll-up
            totals = rx.items.aggregate(total=models.Sum("qty_prescribed"), out=models.Sum("qty_dispensed"))
            if totals["out"] == 0:
                rx.status = RxStatus.PRESCRIBED
            elif totals["out"] < totals["total"]:
                rx.status = RxStatus.PARTIALLY_DISPENSED
            else:
                rx.status = RxStatus.DISPENSED
            rx.save(update_fields=["status"])
        return item
