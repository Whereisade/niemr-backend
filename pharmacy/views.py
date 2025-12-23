import csv, io
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
    mixins.CreateModelMixin,
):
    serializer_class = DrugSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Scope drugs by facility or independent pharmacy user:
        - Facility staff: see drugs belonging to their facility
        - Independent pharmacy (no facility): see drugs they created
        - Admins without facility: see all (for admin dashboards)
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

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsPharmacyStaff])
    def import_csv(self, request):
        """
        CSV columns: code,name,strength,form,route,qty_per_unit,unit_price
        
        Drugs are scoped to the user's facility or to the user (for independent pharmacies).
        """
        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "file is required"}, status=400)
        
        u = request.user
        facility_id = getattr(u, "facility_id", None)
        
        buf = io.StringIO(f.read().decode("utf-8"))
        reader = csv.DictReader(buf)
        created, updated = 0, 0
        
        for row in reader:
            code = (row.get("code") or "").strip()
            if not code:
                continue
            
            defaults = {
                "name": (row.get("name") or "").strip(),
                "strength": (row.get("strength") or "").strip(),
                "form": (row.get("form") or "").strip(),
                "route": (row.get("route") or "").strip(),
                "qty_per_unit": int(row.get("qty_per_unit") or 1),
                "unit_price": (row.get("unit_price") or 0),
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
        
        return Response({"created": created, "updated": updated})


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
                q = q.filter(outsourced_to_id=u.id)
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
                return Response({"detail": "Independent pharmacies can only dispense outsourced prescriptions."}, status=403)

        s = DispenseSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.save(rx=rx, user=request.user)
        rx.refresh_from_db()
        return Response(PrescriptionReadSerializer(rx).data)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def statuses(self, request):
        return Response([c for c, _ in RxStatus.choices])