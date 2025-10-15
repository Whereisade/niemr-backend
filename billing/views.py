import csv, io
from decimal import Decimal
from django.db.models import Sum, F, Q
from django.utils.dateparse import parse_datetime
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Service, Price, Charge, Payment, PaymentAllocation
from .serializers import (
    ServiceSerializer, PriceSerializer,
    ChargeCreateSerializer, ChargeReadSerializer,
    PaymentCreateSerializer, PaymentReadSerializer
)
from .permissions import IsStaff
from .enums import ChargeStatus, PaymentMethod
from notifications.services.notify import notify_user

# --- Service Catalog ---
class ServiceViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin):
    queryset = Service.objects.filter(is_active=True).order_by("name")
    serializer_class = ServiceSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def import_csv(self, request):
        """
        CSV columns: code,name,default_price
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
                "default_price": (row.get("default_price") or 0),
                "is_active": True,
            }
            _, is_created = Service.objects.update_or_create(code=code, defaults=defaults)
            created += int(is_created); updated += int(not is_created)
        return Response({"created": created, "updated": updated})

# --- Facility Prices ---
class PriceViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin):
    serializer_class = PriceSerializer
    permission_classes = [IsAuthenticated, IsStaff]

    def get_queryset(self):
        return Price.objects.filter(facility=self.request.user.facility).select_related("service")

# --- Charges ---
class ChargeViewSet(viewsets.GenericViewSet, mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin):
    queryset = Charge.objects.select_related("patient","facility","service","created_by")
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create",):
            return ChargeCreateSerializer
        return ChargeReadSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user
        if u.role == "PATIENT":
            q = q.filter(patient__user_id=u.id)
        elif u.facility_id:
            q = q.filter(facility_id=u.facility_id)

        # filters
        patient_id = self.request.query_params.get("patient")
        status_ = self.request.query_params.get("status")
        start = self.request.query_params.get("start")
        end   = self.request.query_params.get("end")
        s = self.request.query_params.get("s")

        if patient_id: q = q.filter(patient_id=patient_id)
        if status_: q = q.filter(status=status_)
        if start: q = q.filter(created_at__gte=parse_datetime(start) or start)
        if end: q = q.filter(created_at__lte=parse_datetime(end) or end)
        if s: q = q.filter(Q(description__icontains=s) | Q(service__name__icontains=s) | Q(service__code__icontains=s))

        return q.order_by("-created_at","-id")

    # staff-only charge creation
    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        resp = super().create(request, *args, **kwargs)
        try:
            charge = Charge.objects.get(id=resp.data["id"])
            if charge.patient and charge.patient.user_id:
                notify_user(
                    user=charge.patient.user,
                    topic="BILL_CHARGE_ADDED",
                    title="New charge added",
                    body=f"{charge.service.name} - {charge.amount}",
                    data={"charge_id": charge.id},
                    facility_id=charge.facility_id,
                )
        except Exception:
            pass
        return resp

    @action(detail=False, methods=["get"])
    def statuses(self, request):
        return Response([c for c,_ in ChargeStatus.choices])

    @action(detail=False, methods=["get"])
    def ledger(self, request):
        """
        Patient ledger view. For staff: pass ?patient=<id>
        For patient: implicit to their own.
        Returns totals: charges, payments, balance.
        """
        u = request.user
        patient_id = request.query_params.get("patient")
        if u.role == "PATIENT":
            patient_id = u.patient.id if hasattr(u, "patient") and u.patient else None
        if not patient_id:
            return Response({"detail":"patient is required"}, status=400)

        charges = Charge.objects.filter(patient_id=patient_id)
        if u.role != "PATIENT" and u.facility_id:
            charges = charges.filter(facility_id=u.facility_id)

        ch_total = charges.aggregate(s=Sum("amount"))["s"] or 0
        pay_total = Payment.objects.filter(patient_id=patient_id).aggregate(s=Sum("amount"))["s"] or 0
        balance = Decimal(ch_total) - Decimal(pay_total)
        return Response({"patient_id": int(patient_id), "charges_total": ch_total, "payments_total": pay_total, "balance": balance})

# --- Payments ---
class PaymentViewSet(viewsets.GenericViewSet, mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin):
    queryset = Payment.objects.select_related("patient","facility","received_by")
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create",):
            return PaymentCreateSerializer
        return PaymentReadSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user
        if u.role == "PATIENT":
            q = q.filter(patient__user_id=u.id)
        elif u.facility_id:
            q = q.filter(facility_id=u.facility_id)

        patient_id = self.request.query_params.get("patient")
        start = self.request.query_params.get("start")
        end   = self.request.query_params.get("end")
        if patient_id: q = q.filter(patient_id=patient_id)
        if start: q = q.filter(received_at__gte=parse_datetime(start) or start)
        if end: q = q.filter(received_at__lte=parse_datetime(end) or end)
        return q.order_by("-received_at","-id")

    # staff-only
    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        resp = super().create(request, *args, **kwargs)
        try:
            payment = Payment.objects.get(id=resp.data["id"])
            if payment.patient and payment.patient.user_id:
                notify_user(
                    user=payment.patient.user,
                    topic="BILL_PAYMENT_POSTED",
                    title="Payment received",
                    body=f"Amount: {payment.amount} ({payment.method}) Ref: {payment.reference}",
                    data={"payment_id": payment.id},
                    facility_id=payment.facility_id,
                )
        except Exception:
            pass
        return resp
