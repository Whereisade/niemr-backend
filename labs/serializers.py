from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from accounts.enums import UserRole

from .enums import OrderStatus
from .models import LabTest, LabOrder, LabOrderItem


User = get_user_model()


class LabTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabTest
        fields = [
            "id",
            "code",
            "name",
            "unit",
            "ref_low",
            "ref_high",
            "price",
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
            exists = LabTest.objects.filter(
                code__iexact=code,
                facility_id=u.facility_id,
                is_active=True
            ).exists()
            if exists:
                raise serializers.ValidationError(
                    f"A lab test with code '{code}' already exists in your facility catalog."
                )
        else:
            # Independent lab: check uniqueness within user's tests
            exists = LabTest.objects.filter(
                code__iexact=code,
                facility__isnull=True,
                created_by_id=u.id,
                is_active=True
            ).exists()
            if exists:
                raise serializers.ValidationError(
                    f"A lab test with code '{code}' already exists in your catalog."
                )
        
        return code


class LabOrderItemWriteSerializer(serializers.ModelSerializer):
    """
    Supports either:
    - catalog test selection via test_code
    - manual typed test via requested_name
    """

    test_code = serializers.CharField(write_only=True, required=False, allow_blank=True)
    requested_name = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = LabOrderItem
        fields = [
            "id",
            "test_code",
            "requested_name",
            "sample_collected_at",
            "result_value",
            "result_text",
            "result_unit",
            "ref_low",
            "ref_high",
            "flag",
            "completed_at",
        ]
        read_only_fields = ["flag", "completed_at"]

    def validate(self, attrs):
        code = (attrs.pop("test_code", "") or "").strip()
        name = (attrs.get("requested_name", "") or "").strip()

        if code:
            # Look up test in the orderer's facility catalog or their own catalog
            request = self.context.get("request")
            u = request.user if request else None
            
            test = None
            if u and getattr(u, "facility_id", None):
                # Facility staff: look in facility's catalog
                test = LabTest.objects.filter(
                    code__iexact=code,
                    facility_id=u.facility_id,
                    is_active=True
                ).first()
            elif u:
                # Independent provider: look in their own catalog
                test = LabTest.objects.filter(
                    code__iexact=code,
                    created_by_id=u.id,
                    is_active=True
                ).first()
            
            # Fallback to legacy global tests (facility=None, created_by=None)
            if not test:
                test = LabTest.objects.filter(
                    code__iexact=code,
                    facility__isnull=True,
                    created_by__isnull=True,
                    is_active=True
                ).first()

            # If this is an outsourced order, allow resolving test_code from the outsourced lab's catalog.
            # We read outsourced_to from the parent request payload to avoid coupling nested serializer context.
            if not test and request is not None:
                try:
                    outsourced_to = request.data.get("outsourced_to")
                    if outsourced_to not in (None, "", 0):
                        outsourced_to_id = int(outsourced_to)
                        test = LabTest.objects.filter(
                            code__iexact=code,
                            facility__isnull=True,
                            created_by_id=outsourced_to_id,
                            is_active=True,
                        ).first()
                except Exception:
                    test = None
            
            if not test:
                raise serializers.ValidationError({"test_code": f"Unknown or inactive test_code: {code}"})
            
            attrs["test"] = test
            attrs["requested_name"] = ""
            return attrs

        if not name:
            raise serializers.ValidationError(
                {"detail": "Provide either test_code (catalog) or requested_name (manual)."}
            )

        attrs["test"] = None
        attrs["requested_name"] = name
        return attrs


class LabOrderItemReadSerializer(serializers.ModelSerializer):
    test = LabTestSerializer(allow_null=True)
    display_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = LabOrderItem
        fields = [
            "id",
            "test",
            "requested_name",
            "display_name",
            "sample_collected_at",
            "result_value",
            "result_text",
            "result_unit",
            "ref_low",
            "ref_high",
            "flag",
            "completed_at",
            "completed_by",
        ]

    def get_display_name(self, obj):
        if getattr(obj, "test_id", None):
            return obj.test.name
        return (obj.requested_name or "").strip() or "(Manual test)"


class LabOrderCreateSerializer(serializers.ModelSerializer):
    items = LabOrderItemWriteSerializer(many=True)
    outsourced_to = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = LabOrder
        fields = [
            "id",
            "patient",
            "priority",
            "note",
            "encounter_id",
            "external_lab_name",
            "outsourced_to",
            "items",
        ]

    def validate_outsourced_to(self, value):
        if value in (None, "", 0):
            return None

        user = User.objects.filter(id=value).first()
        if not user:
            raise serializers.ValidationError("Invalid outsourced_to user id")

        role = (getattr(user, "role", "") or "").upper()
        if role != UserRole.LAB:
            raise serializers.ValidationError("outsourced_to must be a LAB user")

        # Outsourcing should target independent providers (no facility)
        if getattr(user, "facility_id", None):
            raise serializers.ValidationError("outsourced_to must be an independent LAB (facility must be null)")

        return user.id

    @transaction.atomic
    def create(self, validated):
        request = self.context["request"]
        user = request.user

        items = validated.pop("items", [])
        outsourced_to_id = validated.pop("outsourced_to", None)

        patient = validated["patient"]

        # Facility linkage:
        # - Facility staff: facility is the user's facility
        # - Independent providers: facility remains NULL (avoid leaking orders into an unrelated facility scope)
        # - System admins (no facility): can bill under patient's facility when applicable
        facility = user.facility if getattr(user, "facility_id", None) else None
        role = (getattr(user, "role", "") or "").upper()
        if not facility and role in {UserRole.ADMIN, UserRole.SUPER_ADMIN} and getattr(patient, "facility_id", None):
            facility = patient.facility

        order = LabOrder.objects.create(
            patient=patient,
            facility=facility,
            ordered_by=user,
            priority=validated.get("priority"),
            note=validated.get("note", ""),
            encounter_id=validated.get("encounter_id"),
            external_lab_name=validated.get("external_lab_name", ""),
            outsourced_to_id=outsourced_to_id,
        )

        for item in items:
            LabOrderItem.objects.create(order=order, **item)

        # Billing: create charges for priced tests.
        try:
            from billing.models import Charge, Service, Price
            from billing.services.pricing import resolve_price
            from decimal import Decimal

            # Avoid duplicates (idempotent-ish)
            if not Charge.objects.filter(lab_order_id=order.id).exists():
                billing_facility = order.facility if order.facility_id and not order.outsourced_to_id else None
                billing_owner = None
                if order.outsourced_to_id:
                    billing_owner = User.objects.filter(id=order.outsourced_to_id).first()
                elif not order.facility_id:
                    # Independent LAB creating its own orders bills itself.
                    actor_role = (getattr(user, "role", "") or "").upper()
                    if actor_role == UserRole.LAB:
                        billing_owner = user

                if billing_facility or billing_owner:
                    for it in order.items.select_related("test").all():
                        if not getattr(it, "test_id", None):
                            continue
                        test = it.test
                        if not test:
                            continue

                        code = f"LAB:{test.code}"
                        service, _ = Service.objects.get_or_create(
                            code=code,
                            defaults={
                                "name": test.name,
                                "default_price": getattr(test, "price", 0) or 0,
                                "is_active": True,
                            },
                        )

                        # Ensure per-scope price exists so independent labs don't inherit someone else's defaults.
                        try:
                            if billing_facility and not billing_owner:
                                Price.objects.get_or_create(
                                    facility=billing_facility,
                                    owner=None,
                                    service=service,
                                    defaults={"amount": getattr(test, "price", 0) or 0, "currency": "NGN"},
                                )
                            if billing_owner and not billing_facility:
                                Price.objects.get_or_create(
                                    facility=None,
                                    owner=billing_owner,
                                    service=service,
                                    defaults={"amount": getattr(test, "price", 0) or 0, "currency": "NGN"},
                                )
                        except Exception:
                            pass

                        unit_price = resolve_price(service=service, facility=billing_facility, owner=billing_owner, patient=order.patient)
                        qty = 1
                        amount = (Decimal(str(unit_price)) * Decimal(qty)).quantize(Decimal("0.01"))
                        Charge.objects.create(
                            patient=order.patient,
                            facility=billing_facility,
                            owner=billing_owner,
                            service=service,
                            description=test.name,
                            unit_price=unit_price,
                            qty=qty,
                            amount=amount,
                            created_by=user,
                            encounter_id=order.encounter_id,
                            lab_order_id=order.id,
                        )
        except Exception:
            # Billing is best-effort; lab workflow should still complete.
            pass

        # Link to encounter (append order.id to encounter.lab_order_ids)
        if order.encounter_id:
            try:
                from encounters.models import Encounter

                enc = Encounter.objects.filter(id=order.encounter_id).first()
                if enc:
                    ids = list(enc.lab_order_ids or [])
                    if order.id not in ids:
                        ids.append(order.id)
                        enc.lab_order_ids = ids
                        enc.save(update_fields=["lab_order_ids", "updated_at"])
            except Exception:
                # Keep order creation resilient; encounter linking is best-effort.
                pass

        return order


class LabOrderReadSerializer(serializers.ModelSerializer):
    items = LabOrderItemReadSerializer(many=True)
    patient_name = serializers.SerializerMethodField(read_only=True)
    outsourced_to_name = serializers.SerializerMethodField(read_only=True)
    patient_insurance_status = serializers.SerializerMethodField(read_only=True)
    patient_hmo_id = serializers.SerializerMethodField(read_only=True)
    patient_hmo_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = LabOrder
        fields = [
            "id",
            "patient",
            "patient_name",
            "patient_insurance_status",
            "patient_hmo_id",
            "patient_hmo_name",
            "facility",
            "ordered_by",
            "priority",
            "status",
            "ordered_at",
            "note",
            "encounter_id",
            "external_lab_name",
            "outsourced_to",
            "outsourced_to_name",
            "items",
        ]

    def get_patient_name(self, obj):
        if not obj.patient_id:
            return None
        first = getattr(obj.patient, "first_name", "") or ""
        last = getattr(obj.patient, "last_name", "") or ""
        name = (first + " " + last).strip()
        return name or str(obj.patient)

    def get_patient_insurance_status(self, obj):
        p = getattr(obj, "patient", None)
        return getattr(p, "insurance_status", None) if p else None

    def get_patient_hmo_id(self, obj):
        p = getattr(obj, "patient", None)
        h = getattr(p, "hmo", None) if p else None
        return getattr(h, "id", None) if h else None

    def get_patient_hmo_name(self, obj):
        p = getattr(obj, "patient", None)
        h = getattr(p, "hmo", None) if p else None
        return getattr(h, "name", None) if h else None

    def get_outsourced_to_name(self, obj):
        u = getattr(obj, "outsourced_to", None)
        if not u:
            return None
        full = (u.get_full_name() or "").strip()
        return full or u.email


class ResultEntrySerializer(serializers.Serializer):
    """Update result for one item."""

    result_value = serializers.DecimalField(max_digits=14, decimal_places=4, required=False, allow_null=True)
    result_text = serializers.CharField(required=False, allow_blank=True)
    ref_low = serializers.DecimalField(max_digits=10, decimal_places=3, required=False, allow_null=True)
    ref_high = serializers.DecimalField(max_digits=10, decimal_places=3, required=False, allow_null=True)

    def save(self, *, item: LabOrderItem, user):
        changed = False
        for f in ("result_value", "result_text", "ref_low", "ref_high"):
            if f in self.validated_data:
                setattr(item, f, self.validated_data[f])
                changed = True

        if changed:
            item.completed_at = timezone.now()
            item.completed_by = user
            item.save()

        order = item.order
        # If all items completed, mark order completed
        if order.items.filter(completed_at__isnull=True).count() == 0:
            order.status = OrderStatus.COMPLETED
            order.save(update_fields=["status"])

        return item