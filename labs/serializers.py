from django.db import transaction
from rest_framework import serializers
from .models import LabTest, LabOrder, LabOrderItem
from .enums import OrderStatus
from django.utils import timezone

class LabTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabTest
        fields = ["id","code","name","unit","ref_low","ref_high","price","is_active"]

class LabOrderItemWriteSerializer(serializers.ModelSerializer):
    test_code = serializers.CharField(write_only=True)

    class Meta:
        model = LabOrderItem
        fields = ["id","test_code","sample_collected_at","result_value","result_text","result_unit","ref_low","ref_high","flag","completed_at"]

    def validate(self, attrs):
        # map test_code -> test
        code = attrs.pop("test_code")
        try:
            test = LabTest.objects.get(code=code, is_active=True)
        except LabTest.DoesNotExist:
            raise serializers.ValidationError(f"Unknown or inactive test_code: {code}")
        attrs["test"] = test
        return attrs

class LabOrderItemReadSerializer(serializers.ModelSerializer):
    test = LabTestSerializer()
    class Meta:
        model = LabOrderItem
        fields = ["id","test","sample_collected_at","result_value","result_text","result_unit","ref_low","ref_high","flag","completed_at","completed_by"]

class LabOrderCreateSerializer(serializers.ModelSerializer):
    items = LabOrderItemWriteSerializer(many=True)

    class Meta:
        model = LabOrder
        fields = ["id","patient","priority","note","encounter_id","external_lab_name","items"]

    @transaction.atomic
    def create(self, validated):
        request = self.context["request"]
        user = request.user
        order = LabOrder.objects.create(
            patient=validated["patient"],
            facility=user.facility if user.facility_id else validated["patient"].facility,
            ordered_by=user,
            priority=validated.get("priority"),
            note=validated.get("note",""),
            encounter_id=validated.get("encounter_id"),
            external_lab_name=validated.get("external_lab_name",""),
        )
        for item in validated["items"]:
            LabOrderItem.objects.create(order=order, **item)
        return order

class LabOrderReadSerializer(serializers.ModelSerializer):
    items = LabOrderItemReadSerializer(many=True)
    class Meta:
        model = LabOrder
        fields = ["id","patient","facility","ordered_by","priority","status","ordered_at","note","encounter_id","external_lab_name","items"]

class ResultEntrySerializer(serializers.Serializer):
    """
    Update result for one item.
    """
    result_value = serializers.DecimalField(max_digits=14, decimal_places=4, required=False, allow_null=True)
    result_text  = serializers.CharField(required=False, allow_blank=True)
    ref_low = serializers.DecimalField(max_digits=10, decimal_places=3, required=False, allow_null=True)
    ref_high = serializers.DecimalField(max_digits=10, decimal_places=3, required=False, allow_null=True)

    def save(self, *, item: LabOrderItem, user):
        changed = False
        for f in ("result_value","result_text","ref_low","ref_high"):
            if f in self.validated_data:
                setattr(item, f, self.validated_data[f])
                changed = True
        if changed:
            item.completed_at = timezone.now()
            item.completed_by = user
            item.save()
        # if all items completed, mark order completed
        order = item.order
        if order.items.filter(completed_at__isnull=True).count() == 0:
            order.status = OrderStatus.COMPLETED
            order.save(update_fields=["status"])
        return item
