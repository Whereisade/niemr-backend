import csv
import io

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts.enums import UserRole
from notifications.services.notify import notify_user

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
    def import_csv(self, request):
        """
        CSV columns: code,name,unit,ref_low,ref_high,price
        
        Tests are scoped to the user's facility or to the user (for independent labs).
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
                "unit": (row.get("unit") or "").strip(),
                "ref_low": (row.get("ref_low") or None),
                "ref_high": (row.get("ref_high") or None),
                "price": (row.get("price") or 0),
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

        return Response({"created": created, "updated": updated})


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
                q = q.filter(outsourced_to_id=u.id)
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

            if order.patient and getattr(order.patient, "user_id", None):
                notify_user(
                    user=order.patient.user,
                    topic="LAB_RESULT_READY",
                    title="Your lab result is ready",
                    body=f"Lab order #{order.id} now has results.",
                    data={"order_id": order.id},
                    facility_id=order.facility_id,
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

        return Response({"detail": "Order cancelled.", "status": order.status}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def statuses(self, request):
        return Response([c for c, _ in OrderStatus.choices])