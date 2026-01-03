from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import models, transaction
from rest_framework import serializers

from accounts.enums import UserRole
from billing.models import Service, Charge, Price
from billing.services.pricing import resolve_price
from notifications.services.notify import notify_user, notify_patient
from notifications.enums import Topic, Priority

from .models import Drug, StockItem, StockTxn, Prescription, PrescriptionItem, DispenseEvent
from .enums import RxStatus, TxnType
from .services.stock_alerts import check_and_notify_low_stock


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
        During updates, exclude the current instance from the uniqueness check.
        """
        request = self.context.get("request")
        if not request or not request.user:
            return value

        u = request.user
        code = (value or "").strip().upper()

        # Get the instance being updated (if this is an update operation)
        instance = getattr(self, 'instance', None)

        if getattr(u, "facility_id", None):
            # Facility staff: check uniqueness within facility
            qs = Drug.objects.filter(
                code__iexact=code,
                facility_id=u.facility_id,
                is_active=True,
            )
            # Exclude the current instance if updating
            if instance:
                qs = qs.exclude(pk=instance.pk)
            
            if qs.exists():
                raise serializers.ValidationError(
                    f"A drug with code '{code}' already exists in your facility catalog."
                )
        else:
            # Independent pharmacy: check uniqueness within user's drugs
            qs = Drug.objects.filter(
                code__iexact=code,
                facility__isnull=True,
                created_by_id=u.id,
                is_active=True,
            )
            # Exclude the current instance if updating
            if instance:
                qs = qs.exclude(pk=instance.pk)
            
            if qs.exists():
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
    # Add computed fields for low stock information
    reorder_threshold = serializers.SerializerMethodField()
    is_low_stock = serializers.SerializerMethodField()
    is_out_of_stock = serializers.SerializerMethodField()

    class Meta:
        model = StockItem
        fields = [
            "id", 
            "facility", 
            "owner", 
            "drug", 
            "drug_id", 
            "current_qty",
            "reorder_level",
            "max_stock_level",
            "reorder_threshold",
            "is_low_stock",
            "is_out_of_stock",
        ]
        read_only_fields = ["facility", "owner", "reorder_threshold", "is_low_stock", "is_out_of_stock"]

    def get_reorder_threshold(self, obj):
        return obj.get_reorder_threshold()

    def get_is_low_stock(self, obj):
        return obj.is_low_stock()

    def get_is_out_of_stock(self, obj):
        return obj.is_out_of_stock()


class StockTxnSerializer(serializers.ModelSerializer):
    drug_name = serializers.SerializerMethodField()
    drug_code = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = StockTxn
        fields = [
            "id",
            "facility",
            "owner",
            "drug",
            "drug_name",
            "drug_code",
            "txn_type",
            "qty",
            "note",
            "created_by",
            "created_by_name",
            "created_at",
        ]
        read_only_fields = ["facility", "owner", "created_by", "created_at"]
    
    def get_drug_name(self, obj):
        return obj.drug.name if obj.drug else None
    
    def get_drug_code(self, obj):
        return obj.drug.code if obj.drug else None
    
    def get_created_by_name(self, obj):
        if not obj.created_by:
            return None
        # Try get_full_name if available, otherwise construct from fields
        if hasattr(obj.created_by, "get_full_name"):
            full = (obj.created_by.get_full_name() or "").strip()
        else:
            first = getattr(obj.created_by, "first_name", "") or ""
            last = getattr(obj.created_by, "last_name", "") or ""
            full = f"{first} {last}".strip()
        return full or getattr(obj.created_by, "email", None)


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

            # If this is an outsourced prescription, allow resolving drug_code from the outsourced pharmacy catalog.
            if not drug and request is not None:
                try:
                    outsourced_to = request.data.get("outsourced_to")
                    if outsourced_to not in (None, "", 0):
                        outsourced_to_id = int(outsourced_to)
                        drug = Drug.objects.filter(
                            code__iexact=code,
                            facility__isnull=True,
                            created_by_id=outsourced_to_id,
                            is_active=True,
                        ).first()
                except Exception:
                    drug = None

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

        # ✅ IMPORTANT: remove patient from validated so it isn't passed twice
        patient = validated.pop("patient")

        # Facility assignment:
        # - Facility-linked staff: always bind to their facility
        # - Independent providers (including independent PHARMACY): facility stays NULL
        # - System admins (no facility): may bind to patient's facility
        facility = u.facility if getattr(u, "facility_id", None) else None
        role = (getattr(u, "role", "") or "").upper()
        if not facility and role in {UserRole.ADMIN, UserRole.SUPER_ADMIN} and getattr(patient, "facility_id", None):
            facility = patient.facility

        rx = Prescription.objects.create(
            patient=patient,
            facility=facility,
            prescribed_by=u,
            outsourced_to_id=outsourced_to_id,
            **validated,  # now contains only encounter_id, note, etc.
        )

        for it in items:
            PrescriptionItem.objects.create(prescription=rx, **it)

        # Notify patient (and guardian/dependents fanout via notify_patient)
        try:
            notify_patient(
                patient=patient,
                topic=Topic.PRESCRIPTION_READY,
                priority=Priority.NORMAL,
                title="New prescription",
                body=f"A new prescription (#{rx.id}) has been created for you.",
                facility_id=getattr(facility, "id", None),
                data={"prescription_id": rx.id},
                action_url="/patient",
                group_key=f"RX:{rx.id}:NEW",
            )
        except Exception:
            pass

        # Notify outsourced pharmacy if applicable
        if outsourced_to_id:
            try:
                outsourced_user = User.objects.filter(id=outsourced_to_id).first()
                if not outsourced_user:
                    raise ValueError("outsourced user not found")

                notify_user(
                    user=outsourced_user,
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
    patient_name = serializers.SerializerMethodField()
    facility_name = serializers.SerializerMethodField()
    prescribed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Prescription
        fields = [
            "id",
            "patient",
            "patient_name",
            "facility",
            "facility_name",
            "prescribed_by",
            "prescribed_by_name",
            "encounter_id",
            "status",
            "note",
            "outsourced_to",
            "outsourced_to_name",
            "created_at",
            "items",
        ]

    def get_patient_name(self, obj):
        p = getattr(obj, "patient", None)
        if not p:
            return None
        # Patient model has first_name and last_name fields
        first = getattr(p, "first_name", "") or ""
        last = getattr(p, "last_name", "") or ""
        full = f"{first} {last}".strip()
        return full or f"Patient #{p.id}"

    def get_facility_name(self, obj):
        f = getattr(obj, "facility", None)
        if not f:
            return None
        return f.name or f"Facility #{f.id}"

    def get_prescribed_by_name(self, obj):
        u = getattr(obj, "prescribed_by", None)
        if not u:
            return None
        # Try get_full_name if available, otherwise construct from fields
        if hasattr(u, "get_full_name"):
            full = (u.get_full_name() or "").strip()
        else:
            first = getattr(u, "first_name", "") or ""
            last = getattr(u, "last_name", "") or ""
            full = f"{first} {last}".strip()
        return full or getattr(u, "email", None) or f"User #{u.id}"

    def get_outsourced_to_name(self, obj):
        u = getattr(obj, "outsourced_to", None)
        if not u:
            return None
        # Try get_full_name if available, otherwise construct from fields
        if hasattr(u, "get_full_name"):
            full = (u.get_full_name() or "").strip()
        else:
            first = getattr(u, "first_name", "") or ""
            last = getattr(u, "last_name", "") or ""
            full = f"{first} {last}".strip()
        return full or getattr(u, "email", None)


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
            elif (not is_outsourced) and role == UserRole.PHARMACY and (not getattr(user, "facility_id", None)) and (rx.prescribed_by_id == getattr(user, "id", None)):
                # Independent pharmacy issuing + dispensing its own prescriptions
                stock_scope = {"owner": user}

        stock_required = bool(stock_scope)

        with transaction.atomic():
            stock_item = None
            
            if stock_required:
                if "facility" in stock_scope:
                    stock_item, _ = StockItem.objects.select_for_update().get_or_create(
                        facility=stock_scope["facility"],
                        owner=None,
                        drug=item.drug,
                        defaults={"current_qty": 0},
                    )
                else:
                    stock_item, _ = StockItem.objects.select_for_update().get_or_create(
                        facility=None,
                        owner=stock_scope["owner"],
                        drug=item.drug,
                        defaults={"current_qty": 0},
                    )

                if stock_item.current_qty < take:
                    raise serializers.ValidationError(
                        f"Insufficient stock for {item.drug.name}. In stock: {stock_item.current_qty}"
                    )

                stock_item.current_qty -= take
                stock_item.save(update_fields=["current_qty"])

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

            # billing when catalog drug exists (facility billing OR owner billing)
            billing_facility = rx.facility if rx.facility_id else None
            billing_owner = None

            # Independent pharmacy collects payment directly for:
            # - outsourced prescriptions assigned to them
            # - prescriptions they issued themselves (self-prescribed workflows)
            if role == UserRole.PHARMACY and not getattr(user, "facility_id", None):
                if rx.outsourced_to_id == getattr(user, "id", None):
                    billing_owner = user
                    billing_facility = None  # force owner-billing even if the Rx has a facility
                elif rx.prescribed_by_id == getattr(user, "id", None) and not rx.outsourced_to_id:
                    billing_owner = user
                    billing_facility = None

            if (billing_facility or billing_owner) and rx.patient and item.drug_id:
                drug = item.drug
                service_code = f"DRUG:{drug.code}"

                service_defaults = {
                    "name": f"{drug.name} {drug.strength}".strip(),
                    "default_price": drug.unit_price,
                    "is_active": True,
                }
                service, _ = Service.objects.get_or_create(code=service_code, defaults=service_defaults)

                # Ensure per-scope price exists for consistent independent pricing.
                try:
                    if billing_facility and not billing_owner:
                        Price.objects.get_or_create(
                            facility=billing_facility,
                            owner=None,
                            service=service,
                            defaults={"amount": drug.unit_price, "currency": "NGN"},
                        )
                    if billing_owner and not billing_facility:
                        Price.objects.get_or_create(
                            facility=None,
                            owner=billing_owner,
                            service=service,
                            defaults={"amount": drug.unit_price, "currency": "NGN"},
                        )
                except Exception:
                    pass

                unit_price = resolve_price(service=service, facility=billing_facility, owner=billing_owner)
                amount = (unit_price or Decimal("0")) * Decimal(take)

                charge = Charge.objects.create(
                    patient=rx.patient,
                    facility=billing_facility,
                    owner=billing_owner,
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
                            facility_id=(billing_facility.id if billing_facility else getattr(rx.patient, "facility_id", None)),
                            action_url="/patient/pharmacy",
                            group_key=f"RX:{rx.id}:DISPENSE",
                        )
                except Exception:
                    pass

            # Check for low stock after dispensing and send notification if needed
            if stock_item is not None:
                try:
                    check_and_notify_low_stock(stock_item)
                except Exception as e:
                    # Log but don't fail the dispense
                    print(f"Failed to check low stock after dispense: {e}")

        return item