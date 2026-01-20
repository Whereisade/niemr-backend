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

        try:
            u = User.objects.get(id=value, is_active=True)
            # Verify the user has lab role
            if u.role not in [UserRole.LAB, UserRole.ADMIN]:
                raise serializers.ValidationError(
                    f"User {u.email} does not have lab scientist role and cannot receive outsourced orders."
                )
            # Independent lab scientist: facility must be null
            if u.facility_id is not None:
                raise serializers.ValidationError(
                    f"Outsourced orders can only be sent to independent lab scientists (not facility staff)."
                )
            return value
        except User.DoesNotExist:
            raise serializers.ValidationError("Lab scientist user not found.")

    @transaction.atomic
    def create(self, validated_data):
        from decimal import Decimal
        import logging
        from billing.models import Service, Price, Charge
        from billing.services.pricing import resolve_price

        logger = logging.getLogger(__name__)
        
        items_data = validated_data.pop("items", [])
        outsourced_to_id = validated_data.pop("outsourced_to", None)

        u = self.context["request"].user
        validated_data["ordered_by"] = u

        # Determine facility context for scoping
        if getattr(u, "facility_id", None):
            validated_data["facility"] = u.facility
        else:
            validated_data["facility"] = None

        # Handle outsourcing
        if outsourced_to_id:
            try:
                outsourced_provider = User.objects.get(id=outsourced_to_id, is_active=True)
                validated_data["outsourced_to"] = outsourced_provider
                # Clear facility if outsourcing to independent provider
                if not outsourced_provider.facility_id:
                    validated_data.pop("facility", None)
            except User.DoesNotExist:
                logger.warning(f"Outsourced provider {outsourced_to_id} not found during order creation")

        order = LabOrder.objects.create(**validated_data)

        # Create items
        for it in items_data:
            LabOrderItem.objects.create(order=order, **it)

        # ========================================================================
        # AUTOMATIC BILLING (if billing app is available)
        # ========================================================================
        try:
            # Determine billing scope
            billing_facility = None
            billing_owner = None

            if order.facility_id:
                billing_facility = order.facility
            elif order.ordered_by and not order.ordered_by.facility_id:
                billing_owner = order.ordered_by

            # Get patient's HMO info (UPDATED for new SystemHMO structure)
            patient_system_hmo = None
            patient_tier = None
            
            if order.patient:
                patient_system_hmo = getattr(order.patient, 'system_hmo', None)
                patient_tier = getattr(order.patient, 'hmo_tier', None)
            
            if billing_facility or billing_owner:
                # Ensure all lab tests have corresponding services
                # This uses the code LAB:{test.code} convention.
                for it in order.items.all():
                    # Skip manual tests (no test FK)
                    if not it.test_id:
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
                    except Exception as e:
                        logger.warning(f"Failed to create price override for service {code}: {e}")

                    # 🔥 FIX: Pass patient's HMO and tier to resolve_price
                    unit_price = resolve_price(
                        service=service, 
                        facility=billing_facility, 
                        owner=billing_owner, 
                        system_hmo=patient_system_hmo,
                        tier=patient_tier
                    )
                    
                    # 🔥 FIX: Handle None pricing gracefully - use test price as fallback
                    if unit_price is None:
                        unit_price = getattr(test, "price", 0) or Decimal("0.00")
                        logger.warning(
                            f"No price configured for lab test {test.code} "
                            f"(facility={billing_facility}, system_hmo={patient_system_hmo}). "
                            f"Using test default price: {unit_price}"
                        )
                    
                    # Convert to Decimal safely
                    unit_price = Decimal(str(unit_price)) if unit_price is not None else Decimal("0.00")
                    qty = 1
                    amount = (unit_price * Decimal(qty)).quantize(Decimal("0.01"))
                    
                    # Create charge
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
                    
                    logger.info(
                        f"Created billing charge for lab order {order.id}, test {test.code}: "
                        f"₦{amount} (patient HMO: {patient_system_hmo.name if patient_system_hmo else 'None'})"
                    )
        except Exception as e:
            # Log the error but don't fail the lab order creation
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to create billing charges for lab order {order.id}: {e}", exc_info=True)

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
    """
    Lab Order Read Serializer with support for new SystemHMO structure.
    
    Includes both legacy HMO fields (for backward compatibility) and new SystemHMO fields.
    """
    items = LabOrderItemReadSerializer(many=True)
    patient_name = serializers.SerializerMethodField(read_only=True)
    outsourced_to_name = serializers.SerializerMethodField(read_only=True)
    
    # ========================================================================
    # LEGACY HMO FIELDS (Backward Compatibility)
    # ========================================================================
    patient_insurance_status = serializers.SerializerMethodField(read_only=True)
    patient_hmo_id = serializers.SerializerMethodField(read_only=True)
    patient_hmo_name = serializers.SerializerMethodField(read_only=True)
    patient_hmo_relationship_status = serializers.SerializerMethodField(read_only=True)
    
    # ========================================================================
    # NEW SYSTEM HMO FIELDS (New HMO Structure)
    # ========================================================================
    # These mirror the fields added to PatientSerializer
    patient_system_hmo_name = serializers.SerializerMethodField(read_only=True)
    patient_hmo_tier_name = serializers.SerializerMethodField(read_only=True)
    patient_hmo_tier_level = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = LabOrder
        fields = [
            "id",
            "patient",
            "patient_name",
            "patient_insurance_status",
            # Legacy HMO fields
            "patient_hmo_id",
            "patient_hmo_name",
            "patient_hmo_relationship_status",
            # New SystemHMO fields
            "patient_system_hmo_name",
            "patient_hmo_tier_name",
            "patient_hmo_tier_level",
            # Order details
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

    # ========================================================================
    # LEGACY HMO GETTERS (for backward compatibility)
    # ========================================================================
    def get_patient_hmo_id(self, obj):
        """Legacy: Get facility-scoped HMO ID"""
        p = getattr(obj, "patient", None)
        h = getattr(p, "hmo", None) if p else None
        return getattr(h, "id", None) if h else None

    def get_patient_hmo_name(self, obj):
        """Legacy: Get facility-scoped HMO name"""
        p = getattr(obj, "patient", None)
        h = getattr(p, "hmo", None) if p else None
        return getattr(h, "name", None) if h else None
    
    def get_patient_hmo_relationship_status(self, obj):
        """
        Get the HMO relationship status for color coding.
        
        This checks:
        1. FacilityHMO relationship status (new system) - if patient has system_hmo
        2. Legacy HMO relationship status (old system) - if patient has legacy hmo
        """
        p = getattr(obj, "patient", None)
        if not p:
            return None
        
        # Try new SystemHMO structure first
        system_hmo = getattr(p, "system_hmo", None)
        if system_hmo:
            # Get FacilityHMO relationship status
            try:
                from patients.models import FacilityHMO
                
                # Determine the scope (facility or independent provider)
                facility = obj.facility
                owner = obj.ordered_by if not facility and obj.ordered_by else None
                
                # Query FacilityHMO
                facility_hmo = None
                if facility:
                    facility_hmo = FacilityHMO.objects.filter(
                        facility=facility,
                        system_hmo=system_hmo,
                        owner__isnull=True
                    ).first()
                elif owner:
                    facility_hmo = FacilityHMO.objects.filter(
                        owner=owner,
                        system_hmo=system_hmo,
                        facility__isnull=True
                    ).first()
                
                if facility_hmo:
                    return facility_hmo.relationship_status
            except Exception:
                pass
        
        # Fallback to legacy HMO relationship status
        h = getattr(p, "hmo", None) if p else None
        return getattr(h, "relationship_status", None) if h else None

    # ========================================================================
    # NEW SYSTEM HMO GETTERS
    # ========================================================================
    def get_patient_system_hmo_name(self, obj):
        """Get SystemHMO name from patient"""
        p = getattr(obj, "patient", None)
        if not p:
            return None
        system_hmo = getattr(p, "system_hmo", None)
        return getattr(system_hmo, "name", None) if system_hmo else None
    
    def get_patient_hmo_tier_name(self, obj):
        """Get HMO tier name from patient"""
        p = getattr(obj, "patient", None)
        if not p:
            return None
        tier = getattr(p, "hmo_tier", None)
        return getattr(tier, "name", None) if tier else None
    
    def get_patient_hmo_tier_level(self, obj):
        """Get HMO tier level (1=Gold, 2=Silver, 3=Bronze) from patient"""
        p = getattr(obj, "patient", None)
        if not p:
            return None
        tier = getattr(p, "hmo_tier", None)
        return getattr(tier, "level", None) if tier else None

    def get_outsourced_to_name(self, obj):
        """
        Get the display name for outsourced lab provider.
        Priority: business_name (from provider profile) > full_name > email
        """
        u = getattr(obj, "outsourced_to", None)
        if not u:
            return None
        
        # Try to get provider profile for business name
        try:
            profile = getattr(u, "provider_profile", None)
            if profile:
                return profile.get_display_name()
        except Exception:
            pass
        
        # Fallback to user name if no profile or no business name
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