import csv
import io
import math

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts.enums import UserRole
from notifications.services.notify import notify_user, notify_patient
from notifications.enums import Topic, Priority

from .enums import OrderStatus
from .models import LabTest, LabOrder, LabOrderItem
from .permissions import CanViewLabOrder, IsLabOrAdmin, IsStaff
from .serializers import (
    LabOrderCreateSerializer,
    LabOrderItemReadSerializer,
    LabOrderReadSerializer,
    LabTestSerializer,
    ResultEntrySerializer,
)
from .services.notify import notify_result_ready


class LabTestViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin):
    serializer_class = LabTestSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Scope lab tests by facility or independent lab user:
        - Facility staff: see tests belonging to their facility
        - Independent lab (no facility): see tests they created
        - Admins without facility: see all (for admin dashboards)
        """
        u = self.request.user
        role = (getattr(u, "role", "") or "").upper()
        
        base_qs = LabTest.objects.filter(is_active=True).order_by("name")
        
        # Facility staff: see their facility's tests
        if getattr(u, "facility_id", None):
            qs = base_qs.filter(facility_id=u.facility_id)
        # Independent lab (no facility): see their own tests
        elif role == UserRole.LAB:
            qs = base_qs.filter(created_by_id=u.id)
        # Admins/Super Admins without facility: can see all for admin purposes
        elif role in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
            qs = base_qs
        # Other independent providers: see tests they created (if any)
        else:
            qs = base_qs.filter(created_by_id=u.id)
        
        # Apply search filter
        s = self.request.query_params.get("s")
        if s:
            qs = qs.filter(Q(name__icontains=s) | Q(code__icontains=s))
        
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        """
        Automatically set facility or created_by based on the user:
        - Facility staff: set facility
        - Independent lab: set created_by
        """
        u = self.request.user
        
        if getattr(u, "facility_id", None):
            # Facility staff: link test to facility
            serializer.save(facility_id=u.facility_id, created_by=u)
        else:
            # Independent lab: link test to user only
            serializer.save(facility=None, created_by=u)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def import_file(self, request):
        """
        Import lab tests from CSV or Excel file.
        
        Required columns: code, name, unit, ref_low, ref_high, price
        Optional columns: is_active (defaults to true)
        
        Supported formats: CSV (.csv), Excel (.xlsx, .xls)
        
        Tests are scoped to the user's facility or to the user (for independent labs).
        """
        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "file is required"}, status=400)

        u = request.user
        facility_id = getattr(u, "facility_id", None)

        filename = f.name.lower()
        created, updated = 0, 0
        errors = []

        try:
            # Determine file type and read data
            if filename.endswith('.csv'):
                # CSV import
                buf = io.StringIO(f.read().decode("utf-8"))
                reader = csv.DictReader(buf)
                rows = list(reader)
            elif filename.endswith(('.xlsx', '.xls')):
                # Excel import
                try:
                    import pandas as pd
                except ImportError:
                    return Response(
                        {"detail": "Excel support not available. Please install pandas and openpyxl."},
                        status=400
                    )
                
                engine = 'openpyxl' if filename.endswith('.xlsx') else None
                df = pd.read_excel(f, engine=engine)
                # Convert DataFrame to list of dicts (similar to csv.DictReader)
                rows = df.to_dict('records')
            else:
                return Response(
                    {"detail": "Unsupported file format. Please upload CSV or Excel (.xlsx, .xls) file."},
                    status=400
                )

            # Validate required columns
            if not rows:
                return Response(
                    {"detail": "File is empty or has no data rows."},
                    status=400
                )
            
            required_columns = ['code', 'name']
            first_row_keys = [k.lower() for k in rows[0].keys()]
            missing_columns = [col for col in required_columns if col not in first_row_keys]
            
            if missing_columns:
                return Response(
                    {"detail": f"Missing required columns: {', '.join(missing_columns)}. Required: code, name"},
                    status=400
                )

            # Process rows
            for idx, row in enumerate(rows, start=2):  # Start at 2 to account for header row
                try:
                    # Normalize column names (case-insensitive)
                    row = {k.lower(): v for k, v in row.items()}
                    
                    code = str(row.get("code") or "").strip()
                    if not code:
                        errors.append(f"Row {idx}: Missing code, skipping")
                        continue
                    
                    name = str(row.get("name") or "").strip()
                    if not name:
                        errors.append(f"Row {idx}: Missing name, skipping")
                        continue

                    # Handle pandas NaN values and type conversions
                    def clean_value(val):
                        """Convert pandas NaN to None, handle empty strings"""
                        if val is None or val == '':
                            return None
                        if isinstance(val, float) and math.isnan(val):
                            return None
                        return val
                    
                    ref_low = clean_value(row.get("ref_low"))
                    ref_high = clean_value(row.get("ref_high"))
                    price = clean_value(row.get("price"))
                    unit = str(row.get("unit") or "").strip()
                    
                    # Convert price to decimal, default to 0
                    try:
                        price = float(price) if price is not None else 0
                    except (ValueError, TypeError):
                        price = 0

                    defaults = {
                        "name": name,
                        "unit": unit,
                        "ref_low": ref_low,
                        "ref_high": ref_high,
                        "price": price,
                        "is_active": True,
                    }

                    if facility_id:
                        # Facility staff: scope to facility
                        _, is_created = LabTest.objects.update_or_create(
                            code=code,
                            facility_id=facility_id,
                            defaults={**defaults, "created_by": u}
                        )
                    else:
                        # Independent lab: scope to user
                        _, is_created = LabTest.objects.update_or_create(
                            code=code,
                            facility=None,
                            created_by=u,
                            defaults=defaults
                        )

                    created += int(is_created)
                    updated += int(not is_created)
                    
                except Exception as e:
                    errors.append(f"Row {idx}: {str(e)}")

        except Exception as e:
            return Response(
                {"detail": f"Failed to process file: {str(e)}"},
                status=400
            )

        response_data = {
            "created": created,
            "updated": updated,
            "total_processed": created + updated,
            "message": f"Successfully imported {created + updated} tests ({created} new, {updated} updated)"
        }
        
        if errors:
            response_data["errors"] = errors[:20]  # Limit to first 20 errors
            response_data["error_count"] = len(errors)
            if len(errors) > 20:
                response_data["message"] += f". Note: {len(errors) - 20} more errors not shown."

        return Response(response_data)


class LabOrderViewSet(
    viewsets.GenericViewSet,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
):
    queryset = (
        LabOrder.objects.select_related("patient", "facility", "ordered_by", "outsourced_to")
        .prefetch_related("items", "items__test")
        .all()
    )
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action in ("create",):
            return LabOrderCreateSerializer
        return LabOrderReadSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user
        role = (getattr(u, "role", "") or "").upper()

        if role == UserRole.PATIENT:
            q = q.filter(patient__user_id=u.id)
        elif getattr(u, "facility_id", None):
            q = q.filter(facility_id=u.facility_id)
        else:
            if role in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
                pass
            elif role == UserRole.LAB:
                # Independent lab should see:
                # - outsourced orders assigned to them
                # - orders they created themselves
                q = q.filter(Q(outsourced_to_id=u.id) | Q(ordered_by_id=u.id))
            else:
                q = q.filter(ordered_by_id=u.id)

        patient_id = self.request.query_params.get("patient")
        if patient_id:
            q = q.filter(patient_id=patient_id)

        status_ = self.request.query_params.get("status")
        if status_:
            q = q.filter(status=status_)

        encounter_id = self.request.query_params.get("encounter")
        if encounter_id:
            q = q.filter(encounter_id=encounter_id)

        s = self.request.query_params.get("s")
        if s:
            q = (
                q.filter(
                    Q(note__icontains=s)
                    | Q(items__test__name__icontains=s)
                    | Q(items__test__code__icontains=s)
                    | Q(items__requested_name__icontains=s)
                )
                .distinct()
            )

        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        if start:
            q = q.filter(ordered_at__gte=parse_datetime(start) or start)
        if end:
            q = q.filter(ordered_at__lte=parse_datetime(end) or end)

        return q

    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewLabOrder]
        self.check_object_permissions(request, obj)
        return Response(LabOrderReadSerializer(obj).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsLabOrAdmin])
    def collect(self, request, pk=None):
        order = self.get_object()

        item_ids = request.data.get("item_ids")
        qs = order.items.all()
        if item_ids:
            qs = qs.filter(id__in=item_ids)

        now = timezone.now()
        updated = qs.update(sample_collected_at=now)

        if updated and order.status == OrderStatus.PENDING:
            order.status = OrderStatus.IN_PROGRESS
            order.save(update_fields=["status"])

        return Response({"detail": f"{updated} items marked as collected."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsLabOrAdmin])
    def result(self, request, pk=None):
        order = self.get_object()

        item_id = request.data.get("item_id")
        if not item_id:
            return Response({"detail": "item_id is required"}, status=400)

        try:
            item = order.items.get(id=item_id)
        except LabOrderItem.DoesNotExist:
            return Response({"detail": "Item not in this order"}, status=404)

        s = ResultEntrySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        item = s.save(item=item, user=request.user)

        if order.status == OrderStatus.COMPLETED:
            if order.patient and getattr(order.patient, "email", None):
                notify_result_ready(order.patient.email, order.id)

            if order.patient:
                notify_patient(
                    patient=order.patient,
                    topic=Topic.LAB_RESULT_READY,
                    priority=Priority.NORMAL,
                    title="Your lab result is ready",
                    body=f"Lab order #{order.id} now has results.",
                    data={"order_id": order.id},
                    facility_id=order.facility_id,
                )

            # Ordering clinician
            if getattr(order, "ordered_by_id", None):
                notify_user(
                    user=order.ordered_by,
                    topic=Topic.LAB_RESULT_READY,
                    priority=Priority.HIGH,
                    title="Lab results ready",
                    body=f"Lab order #{order.id} results are ready for review.",
                    data={"order_id": order.id, "patient_id": order.patient_id},
                    facility_id=order.facility_id,
                    action_url="/facility/labs",
                    group_key=f"LAB:{order.id}:READY",
                )

        return Response(LabOrderItemReadSerializer(item).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def cancel(self, request, pk=None):
        order = self.get_object()

        if order.status in {OrderStatus.COMPLETED, OrderStatus.CANCELLED}:
            return Response(
                {"detail": "Cannot cancel a completed or already cancelled order."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        u = request.user
        role = (getattr(u, "role", "") or "").upper()

        allowed = False
        if role in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
            allowed = True
        elif getattr(u, "facility_id", None) and order.facility_id == u.facility_id:
            allowed = True
        elif order.ordered_by_id == u.id:
            allowed = True

        if not allowed:
            return Response({"detail": "Not allowed to cancel this order."}, status=status.HTTP_403_FORBIDDEN)

        order.status = OrderStatus.CANCELLED
        order.save(update_fields=["status"])

        # Void any billing charges linked to this order (best-effort)
        try:
            from billing.models import Charge
            from billing.enums import ChargeStatus as BillChargeStatus

            Charge.objects.filter(lab_order_id=order.id).update(status=BillChargeStatus.VOID)
        except Exception:
            pass

        return Response({"detail": "Order cancelled.", "status": order.status}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def statuses(self, request):
        return Response([c for c, _ in OrderStatus.choices])