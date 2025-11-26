import csv, io
from django.utils.dateparse import parse_datetime
from django.db.models import Q
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from notifications.services.notify import notify_user

from .models import LabTest, LabOrder, LabOrderItem
from .serializers import (
    LabTestSerializer,
    LabOrderCreateSerializer, LabOrderReadSerializer,
    LabOrderItemReadSerializer, ResultEntrySerializer
)
from .permissions import IsStaff, CanViewLabOrder
from .enums import OrderStatus, Priority
from .services.notify import notify_result_ready


class LabTestViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin):
    queryset = LabTest.objects.filter(is_active=True).order_by("name")
    serializer_class = LabTestSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        # staff only
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def import_csv(self, request):
        """
        CSV columns: code,name,unit,ref_low,ref_high,price
        """
        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "file is required"}, status=400)
        buf = io.StringIO(f.read().decode("utf-8"))
        reader = csv.DictReader(buf)
        created, updated = 0, 0
        for row in reader:
            code = (row.get("code") or "").strip()
            if not code:
                continue
            defaults = {
                "name": (row.get("name") or "").strip(),
                "unit": (row.get("unit") or "").strip(),
                "ref_low": (row.get("ref_low") or None),
                "ref_high": (row.get("ref_high") or None),
                "price": (row.get("price") or 0),
                "is_active": True,
            }
            obj, is_created = LabTest.objects.update_or_create(code=code, defaults=defaults)
            created += int(is_created)
            updated += int(not is_created)
        return Response({"created": created, "updated": updated})


class LabOrderViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    queryset = LabOrder.objects.select_related("patient", "facility", "ordered_by").prefetch_related(
        "items", "items__test"
    )
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    # Only allow GET + POST (no PUT/PATCH/DELETE)
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action in ("create",):
            return LabOrderCreateSerializer
        return LabOrderReadSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user

        # patients: only own orders
        if u.role == "PATIENT":
            q = q.filter(patient__user_id=u.id)
        elif u.facility_id:
            # staff by facility
            q = q.filter(facility_id=u.facility_id)

        # filters
        patient_id = self.request.query_params.get("patient")
        if patient_id:
            q = q.filter(patient_id=patient_id)

        status_ = self.request.query_params.get("status")
        if status_:
            q = q.filter(status=status_)

        s = self.request.query_params.get("s")
        if s:
            q = q.filter(
                Q(note__icontains=s)
                | Q(items__test__name__icontains=s)
                | Q(items__test__code__icontains=s)
            ).distinct()

        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        if start:
            q = q.filter(ordered_at__gte=parse_datetime(start) or start)
        if end:
            q = q.filter(ordered_at__lte=parse_datetime(end) or end)

        return q

    # create: staff only
    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewLabOrder]
        self.check_object_permissions(request, obj)
        return Response(LabOrderReadSerializer(obj).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def collect(self, request, pk=None):
        """
        Mark sample collected for all items or selected item_ids=[...]
        """
        order = self.get_object()
        item_ids = request.data.get("item_ids")
        qs = order.items.all() if not item_ids else order.items.filter(id__in=item_ids)
        n = 0
        for it in qs:
            it.sample_collected_at = it.sample_collected_at or timezone.now()
            it.save(update_fields=["sample_collected_at"])
            n += 1
        order.status = OrderStatus.IN_PROGRESS
        order.save(update_fields=["status"])
        return Response({"updated": n})

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def result(self, request, pk=None):
        """
        Enter result for a single item: payload requires item_id and fields from ResultEntrySerializer
        """
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

        # if order completed, optionally notify patient by email (non-blocking)
        if order.status == OrderStatus.COMPLETED:
            if order.patient and order.patient.email:
                notify_result_ready(order.patient.email, order.id)

            if order.patient and order.patient.user_id:
                notify_user(
                    user=order.patient.user,
                    topic="LAB_RESULT_READY",
                    title="Your lab result is ready",
                    body=f"Lab order #{order.id} now has results.",
                    data={"order_id": order.id},
                    facility_id=order.facility_id,
                )

        return Response(LabOrderItemReadSerializer(item).data)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def statuses(self, request):
        return Response([c for c, _ in OrderStatus.choices])
