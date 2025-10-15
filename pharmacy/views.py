import csv, io
from django.utils.dateparse import parse_datetime
from django.db.models import Q
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Drug, StockItem, StockTxn, Prescription
from .serializers import (
    DrugSerializer, StockItemSerializer, StockTxnSerializer,
    PrescriptionCreateSerializer, PrescriptionReadSerializer, DispenseSerializer
)
from .permissions import IsStaff, CanViewRx
from .enums import RxStatus, TxnType

# --- Catalog ---
class DrugViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin):
    queryset = Drug.objects.filter(is_active=True).order_by("name")
    serializer_class = DrugSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def import_csv(self, request):
        """
        CSV columns: code,name,strength,form,route,qty_per_unit,unit_price
        """
        f = request.FILES.get("file")
        if not f:
            return Response({"detail":"file is required"}, status=400)
        buf = io.StringIO(f.read().decode("utf-8"))
        reader = csv.DictReader(buf)
        created, updated = 0, 0
        for row in reader:
            code = (row.get("code") or "").strip()
            if not code: continue
            defaults = {
                "name": (row.get("name") or "").strip(),
                "strength": (row.get("strength") or "").strip(),
                "form": (row.get("form") or "").strip(),
                "route": (row.get("route") or "").strip(),
                "qty_per_unit": int(row.get("qty_per_unit") or 1),
                "unit_price": (row.get("unit_price") or 0),
                "is_active": True,
            }
            _, is_created = Drug.objects.update_or_create(code=code, defaults=defaults)
            created += int(is_created); updated += int(not is_created)
        return Response({"created": created, "updated": updated})

# --- Stock ---
class StockViewSet(viewsets.GenericViewSet, mixins.ListModelMixin):
    permission_classes = [IsAuthenticated, IsStaff]

    def get_queryset(self):
        return StockItem.objects.select_related("drug","facility").filter(facility=self.request.user.facility)

    def get_serializer_class(self):
        return StockItemSerializer

    @action(detail=False, methods=["post"])
    def adjust(self, request):
        """
        Adjust or add stock for a drug at the current facility.
        payload: { "drug_id": ID, "qty": 100, "note": "Opening balance" } (qty may be +/-)
        """
        u = request.user
        drug_id = request.data.get("drug_id")
        qty = int(request.data.get("qty", 0))
        if not drug_id or qty == 0:
            return Response({"detail":"drug_id and non-zero qty required"}, status=400)
        stock, _ = StockItem.objects.get_or_create(facility=u.facility, drug_id=drug_id, defaults={"current_qty": 0})
        stock.current_qty = max(stock.current_qty + qty, 0)
        stock.save(update_fields=["current_qty"])
        StockTxn.objects.create(
            facility=u.facility, drug_id=drug_id,
            txn_type=TxnType.IN if qty > 0 else TxnType.ADJUST,
            qty=qty, note=request.data.get("note",""),
            created_by=u
        )
        return Response(StockItemSerializer(stock).data, status=201)

    @action(detail=False, methods=["get"])
    def txns(self, request):
        qs = StockTxn.objects.filter(facility=request.user.facility).select_related("drug","created_by").order_by("-created_at")
        return Response(StockTxnSerializer(qs, many=True).data)

# --- Prescriptions ---
class PrescriptionViewSet(viewsets.GenericViewSet, mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin):
    queryset = Prescription.objects.select_related("patient","facility","prescribed_by").prefetch_related("items","items__drug")
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create",):
            return PrescriptionCreateSerializer
        return PrescriptionReadSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user
        if u.role == "PATIENT":
            q = q.filter(patient__user_id=u.id)
        elif u.facility_id:
            q = q.filter(facility_id=u.facility_id)

        patient_id = self.request.query_params.get("patient")
        if patient_id:
            q = q.filter(patient_id=patient_id)

        status_ = self.request.query_params.get("status")
        if status_:
            q = q.filter(status=status_)

        s = self.request.query_params.get("s")
        if s:
            q = q.filter(Q(note__icontains=s) | Q(items__drug__name__icontains=s) | Q(items__drug__code__icontains=s)).distinct()

        start = self.request.query_params.get("start")
        end   = self.request.query_params.get("end")
        if start: q = q.filter(created_at__gte=parse_datetime(start) or start)
        if end:   q = q.filter(created_at__lte=parse_datetime(end) or end)

        return q

    # create: staff only
    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewRx]
        self.check_object_permissions(request, obj)
        return Response(PrescriptionReadSerializer(obj).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def dispense(self, request, pk=None):
        """
        Dispense a quantity of one item in the prescription.
        { "item_id": <id>, "qty": 10, "note": "Issued 10 tabs" }
        """
        rx = self.get_object()
        s = DispenseSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        item = s.save(rx=rx, user=request.user)
        return Response(PrescriptionReadSerializer(rx).data)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def statuses(self, request):
        return Response([c for c,_ in RxStatus.choices])
