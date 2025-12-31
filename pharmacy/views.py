import csv
import io
import math

from django.utils.dateparse import parse_datetime
from django.db.models import Q
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts.enums import UserRole
from .models import Drug, StockItem, StockTxn, Prescription
from .serializers import (
    DrugSerializer,
    StockItemSerializer,
    StockTxnSerializer,
    PrescriptionCreateSerializer,
    PrescriptionReadSerializer,
    DispenseSerializer,
)
from .permissions import IsStaff, CanViewRx, IsPharmacyStaff, CanPrescribe
from .enums import RxStatus, TxnType


# --- Catalog ---
class DrugViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    serializer_class = DrugSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        # Anyone authenticated can view the catalog within their scope.
        # Only pharmacy staff (or admins) can modify items (create/update/delete/import).
        if self.action in {"create", "update", "partial_update", "destroy", "import_file", "clear_catalog"}:
            return [IsPharmacyStaff()]
        return [IsAuthenticated()]

    def get_queryset(self):
        """
        Scope drugs by facility or independent pharmacy user:
        - Facility staff: see drugs belonging to their facility
        - Independent pharmacy (no facility): see drugs they created
        - Admins without facility: see all (for admin dashboards)
        - Patients: see all active drugs (for appointment booking)
        """
        u = self.request.user
        role = (getattr(u, "role", "") or "").upper()
        
        base_qs = Drug.objects.filter(is_active=True).order_by("name")
        
        # Facility staff: see their facility's drugs
        if getattr(u, "facility_id", None):
            return base_qs.filter(facility_id=u.facility_id)
        
        # Independent pharmacy (no facility): see their own drugs
        if role == UserRole.PHARMACY:
            return base_qs.filter(created_by_id=u.id)
        
        # Admins/Super Admins without facility: can see all for admin purposes
        if role in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
            return base_qs
        
        # Patients: can see all active drugs for appointment booking
        if role == UserRole.PATIENT:
            return base_qs
        
        # Other independent providers: see drugs they created (if any)
        return base_qs.filter(created_by_id=u.id)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsPharmacyStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        """
        Automatically set facility or created_by based on the user:
        - Facility staff: set facility
        - Independent pharmacy: set created_by
        """
        u = self.request.user
        
        if getattr(u, "facility_id", None):
            # Facility staff: link drug to facility
            serializer.save(facility_id=u.facility_id, created_by=u)
        else:
            # Independent pharmacy: link drug to user only
            serializer.save(facility=None, created_by=u)

    def destroy(self, request, *args, **kwargs):
        """
        Soft delete a drug by setting is_active=False.
        Only allow deletion if user owns the drug (facility or created_by match).
        """
        instance = self.get_object()
        u = request.user
        role = (getattr(u, "role", "") or "").upper()
        
        # Check ownership
        can_delete = False
        
        if role in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
            can_delete = True
        elif getattr(u, "facility_id", None) and instance.facility_id == u.facility_id:
            can_delete = True
        elif not instance.facility_id and instance.created_by_id == u.id:
            can_delete = True
        
        if not can_delete:
            return Response(
                {"detail": "You do not have permission to delete this drug."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Soft delete
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        
        return Response(
            {"detail": "Drug deleted successfully."},
            status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["delete"], permission_classes=[IsAuthenticated, IsPharmacyStaff])
    def clear_catalog(self, request):
        """
        Clear entire drug catalog by soft deleting all drugs in user's scope.
        - Facility staff: clear their facility's drugs
        - Independent pharmacy: clear their own drugs
        - Admins: must specify scope (not allowed to clear all)
        """
        u = request.user
        role = (getattr(u, "role", "") or "").upper()
        
        # Prevent admins from accidentally clearing everything
        if role in {UserRole.ADMIN, UserRole.SUPER_ADMIN} and not getattr(u, "facility_id", None):
            return Response(
                {"detail": "Admins must be scoped to a facility to clear catalog. This prevents accidental deletion of all drugs."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Determine scope and clear
        if getattr(u, "facility_id", None):
            # Facility staff: clear facility's drugs
            count = Drug.objects.filter(
                facility_id=u.facility_id,
                is_active=True
            ).update(is_active=False)
        elif role == UserRole.PHARMACY:
            # Independent pharmacy: clear their drugs
            count = Drug.objects.filter(
                created_by_id=u.id,
                is_active=True
            ).update(is_active=False)
        else:
            return Response(
                {"detail": "You do not have permission to clear the catalog."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        return Response(
            {
                "detail": f"Catalog cleared successfully. {count} drug(s) deleted.",
                "count": count
            },
            status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsPharmacyStaff])
    def import_file(self, request):
        """
        Import drugs from CSV or Excel file.
        
        Required columns: code, name
        Optional columns: strength, form, route, qty_per_unit, unit_price, is_active
        
        Supported formats: CSV (.csv), Excel (.xlsx, .xls)
        
        Drugs are scoped to the user's facility or to the user (for independent pharmacies).
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
                    def clean_value(val, default=''):
                        """Convert pandas NaN to default, handle empty strings"""
                        if val is None or val == '':
                            return default
                        if isinstance(val, float) and math.isnan(val):
                            return default
                        return str(val).strip()
                    
                    strength = clean_value(row.get("strength"))
                    form = clean_value(row.get("form"))
                    route = clean_value(row.get("route"))
                    
                    # Handle qty_per_unit
                    qty_per_unit = row.get("qty_per_unit")
                    try:
                        if qty_per_unit is None or qty_per_unit == '' or (isinstance(qty_per_unit, float) and math.isnan(qty_per_unit)):
                            qty_per_unit = 1
                        else:
                            qty_per_unit = int(float(qty_per_unit))
                            if qty_per_unit <= 0:
                                qty_per_unit = 1
                    except (ValueError, TypeError):
                        qty_per_unit = 1
                    
                    # Handle unit_price
                    unit_price = row.get("unit_price")
                    try:
                        if unit_price is None or unit_price == '' or (isinstance(unit_price, float) and math.isnan(unit_price)):
                            unit_price = 0
                        else:
                            unit_price = float(unit_price)
                    except (ValueError, TypeError):
                        unit_price = 0
                    
                    defaults = {
                        "name": name,
                        "strength": strength,
                        "form": form,
                        "route": route,
                        "qty_per_unit": qty_per_unit,
                        "unit_price": unit_price,
                        "is_active": True,
                    }
                    
                    if facility_id:
                        # Facility staff: scope to facility
                        _, is_created = Drug.objects.update_or_create(
                            code=code,
                            facility_id=facility_id,
                            defaults={**defaults, "created_by": u}
                        )
                    else:
                        # Independent pharmacy: scope to user
                        _, is_created = Drug.objects.update_or_create(
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
            "message": f"Successfully imported {created + updated} drugs ({created} new, {updated} updated)"
        }
        
        if errors:
            response_data["errors"] = errors[:20]  # Limit to first 20 errors
            response_data["error_count"] = len(errors)
            if len(errors) > 20:
                response_data["message"] += f". Note: {len(errors) - 20} more errors not shown."
        
        return Response(response_data)


# --- Stock ---
class StockViewSet(viewsets.GenericViewSet, mixins.ListModelMixin):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsPharmacyStaff]

    def _scope(self, user):
        role = (getattr(user, "role", "") or "").upper()

        # Facility pharmacy stock
        if getattr(user, "facility_id", None):
            return {"facility": user.facility, "owner": None}

        # Independent pharmacy stock (tracked per owner)
        if role == UserRole.PHARMACY:
            return {"facility": None, "owner": user}

        return None

    def get_queryset(self):
        scope = self._scope(self.request.user)
        if not scope:
            return StockItem.objects.none()

        qs = StockItem.objects.select_related("drug")
        if scope["facility"]:
            return qs.filter(facility=scope["facility"])
        return qs.filter(owner=scope["owner"])

    def get_serializer_class(self):
        return StockItemSerializer

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsPharmacyStaff])
    def adjust(self, request):
        scope = self._scope(request.user)
        if not scope:
            return Response({"detail": "You do not have access to pharmacy stock."}, status=403)

        u = request.user
        drug_id = request.data.get("drug_id")
        qty = int(request.data.get("qty", 0))
        if not drug_id or qty == 0:
            return Response({"detail": "drug_id and non-zero qty required"}, status=400)

        stock, _ = StockItem.objects.get_or_create(
            facility=scope["facility"],
            owner=scope["owner"],
            drug_id=drug_id,
            defaults={"current_qty": 0},
        )
        stock.current_qty = max(stock.current_qty + qty, 0)
        stock.save(update_fields=["current_qty"])

        StockTxn.objects.create(
            facility=scope["facility"],
            owner=scope["owner"],
            drug_id=drug_id,
            txn_type=TxnType.IN if qty > 0 else TxnType.ADJUST,
            qty=qty,
            note=request.data.get("note", ""),
            created_by=u,
        )
        return Response(StockItemSerializer(stock).data, status=201)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated, IsPharmacyStaff])
    def txns(self, request):
        scope = self._scope(request.user)
        if not scope:
            return Response({"detail": "You do not have access to pharmacy stock."}, status=403)

        qs = StockTxn.objects.all()
        if scope["facility"]:
            qs = qs.filter(facility=scope["facility"])
        else:
            qs = qs.filter(owner=scope["owner"])

        qs = qs.select_related("drug", "created_by").order_by("-created_at")
        return Response(StockTxnSerializer(qs, many=True).data)


# --- Prescriptions ---

class PrescriptionViewSet(
    viewsets.GenericViewSet,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
):
    queryset = (
        Prescription.objects.select_related("patient", "facility", "prescribed_by", "outsourced_to")
        .prefetch_related("items", "items__drug")
    )
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create",):
            return PrescriptionCreateSerializer
        return PrescriptionReadSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user
        role = (getattr(u, "role", "") or "").upper()

        if role == UserRole.PATIENT:
            q = q.filter(patient__user_id=u.id)
        elif getattr(u, "facility_id", None):
            q = q.filter(facility_id=u.facility_id)
        else:
            # independent (no facility)
            if role in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
                pass
            elif role == UserRole.PHARMACY:
                # Independent pharmacy should see:
                # - outsourced prescriptions assigned to them
                # - prescriptions they created themselves (self-prescribed workflows)
                q = q.filter(Q(outsourced_to_id=u.id) | Q(prescribed_by_id=u.id))
            else:
                q = q.filter(prescribed_by_id=u.id)

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
            q = q.filter(
                Q(note__icontains=s)
                | Q(items__drug__name__icontains=s)
                | Q(items__drug__code__icontains=s)
                | Q(items__drug_name__icontains=s)
            ).distinct()

        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        if start:
            q = q.filter(created_at__gte=parse_datetime(start) or start)
        if end:
            q = q.filter(created_at__lte=parse_datetime(end) or end)

        return q

    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, CanPrescribe]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewRx]
        self.check_object_permissions(request, obj)
        return Response(PrescriptionReadSerializer(obj).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsPharmacyStaff])
    def dispense(self, request, pk=None):
        rx = self.get_object()
        u = request.user
        role = (getattr(u, "role", "") or "").upper()

        # If outsourced, ONLY assigned pharmacy (or admins) can dispense
        if rx.outsourced_to_id:
            if role not in {UserRole.ADMIN, UserRole.SUPER_ADMIN} and u.id != rx.outsourced_to_id:
                return Response({"detail": "This prescription is outsourced to another pharmacy."}, status=403)

        # If not outsourced, facility pharmacy must match facility (admins can bypass)
        if not rx.outsourced_to_id and role not in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
            if u.facility_id and rx.facility_id != u.facility_id:
                return Response({"detail": "Prescription is not in your facility."}, status=403)
            if not u.facility_id:
                # Independent pharmacies can dispense:
                # - outsourced prescriptions assigned to them (handled above)
                # - their own prescriptions they issued (self-prescribed workflows)
                if role != UserRole.PHARMACY or rx.prescribed_by_id != u.id:
                    return Response({"detail": "Independent pharmacies can only dispense outsourced prescriptions or their own prescriptions."}, status=403)

        s = DispenseSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.save(rx=rx, user=request.user)
        rx.refresh_from_db()
        return Response(PrescriptionReadSerializer(rx).data)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def statuses(self, request):
        return Response([c for c, _ in RxStatus.choices])