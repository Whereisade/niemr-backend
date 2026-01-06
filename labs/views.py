import csv
import io
import math

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts.enums import UserRole
from patients.models import HMO
from notifications.services.notify import notify_user, notify_patient
from notifications.enums import Topic, Priority
from facilities.permissions_utils import has_facility_permission
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


class LabTestViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin):
    """
    ViewSet for managing lab test catalog.
    Now includes update capability for editing test details like price.
    """
    serializer_class = LabTestSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Scope lab tests by facility or independent lab user:
        - Facility staff: see tests belonging to their facility
        - Independent lab (no facility): see tests they created
        - Admins without facility: see all (for admin dashboards)
        - Patients: see all active tests (for appointment booking)
        """
        u = self.request.user
        role = (getattr(u, "role", "") or "").upper()
        
        base_qs = LabTest.objects.filter(is_active=True).order_by("name")
        
        # Facility staff: see their facility's tests
        if getattr(u, "facility_id", None):
            return base_qs.filter(facility_id=u.facility_id)
        
        # Independent lab (no facility): see their own tests
        if role == UserRole.LAB:
            return base_qs.filter(created_by_id=u.id)
        
        # Admins/Super Admins without facility: can see all for admin purposes
        if role in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
            return base_qs
        
        # Patients: can see all active tests for appointment booking
        if role == UserRole.PATIENT:
            return base_qs
        
        # Other independent providers: see tests they created (if any)
        return base_qs.filter(created_by_id=u.id)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    def create(self, request, *args, **kwargs):
        if not has_facility_permission(request.user, 'can_manage_lab_catalog'):
            return Response(
                {"detail": "You do not have permission to manage the lab catalog."},
                status=status.HTTP_403_FORBIDDEN
            )
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)
    
    def update(self, request, *args, **kwargs):
        if not has_facility_permission(request.user, 'can_manage_lab_catalog'):
            return Response(
                {"detail": "You do not have permission to manage the lab catalog."},
                status=status.HTTP_403_FORBIDDEN
            )
        """Update a lab test (full update)"""
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        
        instance = self.get_object()
        
        # Check ownership
        u = request.user
        if getattr(u, "facility_id", None):
            if instance.facility_id != u.facility_id:
                return Response(
                    {"detail": "You can only update tests from your facility."},
                    status=status.HTTP_403_FORBIDDEN
                )
        else:
            role = (getattr(u, "role", "") or "").upper()
            if role not in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
                if instance.created_by_id != u.id:
                    return Response(
                        {"detail": "You can only update tests you created."},
                        status=status.HTTP_403_FORBIDDEN
                    )
        
        return super().update(request, *args, **kwargs)
    
    def partial_update(self, request, *args, **kwargs):
        """Update a lab test (partial update)"""
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        
        instance = self.get_object()
        
        # Check ownership
        u = request.user
        if getattr(u, "facility_id", None):
            if instance.facility_id != u.facility_id:
                return Response(
                    {"detail": "You can only update tests from your facility."},
                    status=status.HTTP_403_FORBIDDEN
                )
        else:
            role = (getattr(u, "role", "") or "").upper()
            if role not in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
                if instance.created_by_id != u.id:
                    return Response(
                        {"detail": "You can only update tests you created."},
                        status=status.HTTP_403_FORBIDDEN
                    )
        
        return super().partial_update(request, *args, **kwargs)
    
    def destroy(self, request, *args, **kwargs):
        if not has_facility_permission(request.user, 'can_manage_lab_catalog'):
            return Response(
                {"detail": "You do not have permission to manage the lab catalog."},
                status=status.HTTP_403_FORBIDDEN
            )
        """Delete a single lab test"""
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        
        instance = self.get_object()
        
        # Check ownership
        u = request.user
        if getattr(u, "facility_id", None):
            if instance.facility_id != u.facility_id:
                return Response(
                    {"detail": "You can only delete tests from your facility."},
                    status=status.HTTP_403_FORBIDDEN
                )
        else:
            role = (getattr(u, "role", "") or "").upper()
            if role not in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
                if instance.created_by_id != u.id:
                    return Response(
                        {"detail": "You can only delete tests you created."},
                        status=status.HTTP_403_FORBIDDEN
                    )
        
        # Soft delete by setting is_active to False
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        
        return Response(
            {"detail": "Lab test deleted successfully."},
            status=status.HTTP_200_OK
        )

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
        if not has_facility_permission(request.user, 'can_manage_lab_catalog'):
            return Response(
                {"detail": "You do not have permission to manage the lab catalog."},
                status=status.HTTP_403_FORBIDDEN
            )

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

    @action(detail=False, methods=["delete"], permission_classes=[IsAuthenticated, IsStaff])
    def clear_catalog(self, request):
        if not has_facility_permission(request.user, 'can_manage_lab_catalog'):
            return Response(
                {"detail": "You do not have permission to manage the lab catalog."},
                status=status.HTTP_403_FORBIDDEN
            )
        """
        Delete all lab tests in the user's scope (facility or user-created).
        This is a soft delete (sets is_active=False).
        """
        u = request.user
        role = (getattr(u, "role", "") or "").upper()
        
        # Build queryset based on user scope
        if getattr(u, "facility_id", None):
            # Facility staff: clear their facility's catalog
            qs = LabTest.objects.filter(facility_id=u.facility_id, is_active=True)
        elif role == UserRole.LAB:
            # Independent lab: clear their own catalog
            qs = LabTest.objects.filter(created_by_id=u.id, is_active=True)
        elif role in {UserRole.ADMIN, UserRole.SUPER_ADMIN}:
            return Response(
                {"detail": "Admins cannot clear the entire catalog. Please specify a facility or user scope."},
                status=status.HTTP_403_FORBIDDEN
            )
        else:
            # Other independent providers: clear tests they created
            qs = LabTest.objects.filter(created_by_id=u.id, is_active=True)
        
        count = qs.count()
        if count == 0:
            return Response(
                {"detail": "No active tests found in your catalog."},
                status=status.HTTP_200_OK
            )
        
        # Soft delete
        qs.update(is_active=False)
        
        return Response(
            {
                "detail": f"Successfully deleted {count} lab test(s) from your catalog.",
                "deleted_count": count
            },
            status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated, IsStaff], url_path="hmo-catalog")
    def hmo_catalog(self, request):
        """
        Get facility lab catalog with HMO-specific prices.
        
        Query params:
        - hmo or hmo_id: Required HMO ID
        
        Returns:
        - Full test catalog with hmo_price field showing HMO override (if any)
        - All columns from general catalog plus HMO pricing
        
        Example:
        GET /api/labs/catalog/hmo-catalog/?hmo_id=1
        """
        u = request.user
        facility_id = getattr(u, "facility_id", None)
        if not facility_id:
            return Response({"detail": "HMO catalogs are only available for facility accounts."}, status=400)

        hmo_id = request.query_params.get("hmo") or request.query_params.get("hmo_id")
        if not hmo_id:
            return Response({"detail": "hmo (or hmo_id) query param is required"}, status=400)

        from patients.models import HMO
        hmo = get_object_or_404(HMO, id=hmo_id, facility_id=facility_id, is_active=True)

        # Get facility tests
        qs = LabTest.objects.filter(facility_id=facility_id, is_active=True).order_by("name", "code")
        
        # Fetch HMO price overrides
        from billing.models import HMOPrice, Service
        tests_data = []
        
        for test in qs:
            svc_code = f"LAB:{test.code}"
            
            # Get HMO price override if it exists
            hmo_price = None
            try:
                service = Service.objects.filter(code=svc_code).first()
                if service:
                    hmo_price_obj = HMOPrice.objects.filter(
                        facility_id=facility_id,
                        hmo=hmo,
                        service=service,
                        is_active=True,
                    ).first()
                    if hmo_price_obj:
                        hmo_price = str(hmo_price_obj.amount)
            except Exception:
                pass
            
            # Build response with complete data and clear field names
            tests_data.append({
                "test_id": test.id,
                "test_code": test.code,
                "test_name": test.name,
                "unit": test.unit,
                "ref_low": test.ref_low,
                "ref_high": test.ref_high,
                "catalog_price": str(test.price),
                "is_active": test.is_active,
                "hmo_id": hmo.id,
                "hmo_name": hmo.name,
                "hmo_price": hmo_price,  # None if no override
            })
        
        return Response(tests_data)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsStaff], url_path="set-hmo-price")
    def set_hmo_price(self, request):
        """
        Set HMO-specific price for a single lab test.
        
        Body:
        {
        "hmo_id": 1,
        "code": "FBC_HB",  // OR "test_id": 123
        "amount": 2500.00  // OR "price": 2500.00
        }
        
        Accepts both test_id and code for flexibility.
        """
        u = request.user
        facility_id = getattr(u, "facility_id", None)
        if not facility_id:
            return Response({"detail": "HMO pricing is only available for facility accounts."}, status=400)

        hmo_id = request.data.get("hmo_id") or request.data.get("hmo")
        code = request.data.get("code")
        test_id = request.data.get("test_id")
        amount = request.data.get("amount") or request.data.get("price")

        if not hmo_id:
            return Response({"detail": "hmo_id is required"}, status=400)
        
        if not (code or test_id):
            return Response({"detail": "Either code or test_id is required"}, status=400)
        
        if amount is None:
            return Response({"detail": "amount is required"}, status=400)

        from patients.models import HMO
        hmo = get_object_or_404(HMO, id=hmo_id, facility_id=facility_id, is_active=True)
        
        # Get test by ID or code
        if test_id:
            test = get_object_or_404(LabTest, id=test_id, facility_id=facility_id, is_active=True)
        else:
            test = get_object_or_404(LabTest, facility_id=facility_id, code=str(code).strip(), is_active=True)

        from billing.models import Service, HMOPrice

        # Create/update service code
        svc_code = f"LAB:{test.code}"
        service, _ = Service.objects.get_or_create(
            code=svc_code,
            defaults={
                "name": test.name or svc_code,
                "default_price": test.price or 0,
            },
        )
        
        # Create/update HMO price override
        hp, created = HMOPrice.objects.update_or_create(
            facility_id=facility_id,
            hmo=hmo,
            service=service,
            defaults={"amount": amount, "currency": "NGN", "is_active": True},
        )
        
        return Response({
            "ok": True,
            "test_id": test.id,
            "test_code": test.code,
            "service": service.code,
            "hmo_price": str(hp.amount),
            "created": created
        })

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsStaff], url_path="import-hmo-file")
    def import_hmo_file(self, request):
        """
        Bulk import HMO-specific prices from CSV/Excel.
        
        Query params:
        - hmo or hmo_id: Required HMO ID
        
        File format (CSV/Excel):
        - Required columns: code, price
        - Optional columns: name, unit, ref_low, ref_high
        - Will auto-create tests if they don't exist
        
        Example CSV:
        code,name,unit,ref_low,ref_high,price
        FBC_HB,Hemoglobin,g/dL,12.0,16.0,2500.00
        FBS,Fasting Blood Sugar,mg/dL,70.0,100.0,1500.00
        
        Example:
        POST /api/labs/catalog/import-hmo-file/?hmo_id=1
        Content-Type: multipart/form-data
        
        file: [CSV/Excel file]
        """
        u = request.user
        facility_id = getattr(u, "facility_id", None)
        if not facility_id:
            return Response({"detail": "HMO catalogs are only available for facility accounts."}, status=400)

        hmo_id = request.query_params.get("hmo") or request.query_params.get("hmo_id") or request.data.get("hmo_id")
        if not hmo_id:
            return Response({"detail": "hmo (or hmo_id) is required"}, status=400)

        from patients.models import HMO
        hmo = get_object_or_404(HMO, id=hmo_id, facility_id=facility_id, is_active=True)

        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "file is required"}, status=400)

        filename = f.name.lower()
        updated = 0
        created = 0
        errors = []

        try:
            # Parse file
            if filename.endswith(".csv"):
                buf = io.StringIO(f.read().decode("utf-8"))
                reader = csv.DictReader(buf)
                rows = list(reader)
            elif filename.endswith((".xlsx", ".xls")):
                try:
                    import pandas as pd
                except ImportError:
                    return Response(
                        {"detail": "Excel support not available. Please install pandas and openpyxl."},
                        status=400,
                    )
                engine = "openpyxl" if filename.endswith(".xlsx") else None
                df = pd.read_excel(f, engine=engine)
                rows = df.to_dict("records")
            else:
                return Response(
                    {"detail": "Unsupported file format. Please upload CSV or Excel (.xlsx, .xls) file."},
                    status=400,
                )

            if not rows:
                return Response({"detail": "File is empty or has no data rows."}, status=400)

            # Validate columns
            required_columns = ["code", "price"]
            first_row_keys = [str(k).lower() for k in rows[0].keys()]
            missing_columns = [col for col in required_columns if col not in first_row_keys]
            if missing_columns:
                return Response(
                    {"detail": f"Missing required columns: {', '.join(missing_columns)}"},
                    status=400,
                )

            from billing.models import Service, HMOPrice

            # Process rows
            for i, row in enumerate(rows, start=2):
                try:
                    normalized = {str(k).lower(): v for k, v in row.items()}
                    code = str(normalized.get("code") or "").strip()
                    if not code:
                        continue
                    amount = normalized.get("price")
                    if amount is None or str(amount).strip() == "":
                        continue

                    # Get or create test
                    test = LabTest.objects.filter(facility_id=facility_id, code=code).first()
                    if not test:
                        import math
                        
                        def clean_decimal(val):
                            if val is None or val == '' or (isinstance(val, float) and math.isnan(val)):
                                return None
                            try:
                                return float(val)
                            except:
                                return None
                        
                        name = str(normalized.get("name") or code).strip()
                        test = LabTest.objects.create(
                            facility_id=facility_id,
                            created_by=u,
                            code=code,
                            name=name,
                            unit=str(normalized.get("unit") or "").strip(),
                            ref_low=clean_decimal(normalized.get("ref_low")),
                            ref_high=clean_decimal(normalized.get("ref_high")),
                            price=amount,
                            is_active=True,
                        )
                        created += 1

                    # Create/update service and HMO price
                    svc_code = f"LAB:{test.code}"
                    service, _ = Service.objects.get_or_create(
                        code=svc_code,
                        defaults={
                            "name": test.name or svc_code,
                            "default_price": test.price or 0,
                        },
                    )
                    HMOPrice.objects.update_or_create(
                        facility_id=facility_id,
                        hmo=hmo,
                        service=service,
                        defaults={"amount": amount, "currency": "NGN", "is_active": True},
                    )
                    updated += 1
                except Exception as e:
                    errors.append({"row": i, "error": str(e)})

            return Response({
                "updated": updated,
                "created": created,
                "total_processed": updated + created,
                "errors": errors[:20] if errors else [],
                "error_count": len(errors),
                "message": f"Successfully processed {updated + created} items ({created} tests created, {updated} prices updated)"
            }, status=200)
        except Exception as e:
            return Response({"detail": f"Import failed: {str(e)}"}, status=400)

# LabOrderViewSet remains the same...
class LabOrderViewSet(
    viewsets.GenericViewSet,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
):
    queryset = (
        LabOrder.objects.select_related("patient", "patient__hmo", "facility", "ordered_by", "outsourced_to")
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

        coverage = self.request.query_params.get("coverage")
        if coverage:
            c = (coverage or "").strip().lower()
            if c in {"insured", "hmo"}:
                q = q.filter(patient__insurance_status="INSURED")
            elif c in {"uninsured", "self_pay", "selfpay"}:
                q = q.filter(Q(patient__insurance_status="SELF_PAY") | Q(patient__insurance_status__isnull=True))


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
        if not has_facility_permission(request.user, 'can_process_lab_orders'):
            return Response(
                {"detail": "You do not have permission to process lab orders."},
                status=status.HTTP_403_FORBIDDEN
            )
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
        if not has_facility_permission(request.user, 'can_process_lab_orders'):
            return Response(
                {"detail": "You do not have permission to enter lab results."},
                status=status.HTTP_403_FORBIDDEN
            )
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