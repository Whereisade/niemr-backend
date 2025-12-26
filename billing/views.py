import csv, io
from decimal import Decimal
from django.db.models import Sum, F, Q
from django.utils.dateparse import parse_datetime
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import Service, Price, Charge, Payment, PaymentAllocation
from .serializers import (
    ServiceSerializer, PriceSerializer,
    ChargeCreateSerializer, ChargeReadSerializer,
    PaymentCreateSerializer, PaymentReadSerializer
)
from .permissions import IsStaff
from .enums import ChargeStatus, PaymentMethod
from notifications.services.notify import notify_user, notify_patient
from notifications.enums import Topic, Priority

# --- Service Catalog ---
class ServiceViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin):
    queryset = Service.objects.filter(is_active=True).order_by("name")
    serializer_class = ServiceSerializer
    authentication_classes = [JWTAuthentication]
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
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsStaff]

    def get_queryset(self):
        u = self.request.user
        role = (getattr(u, "role", "") or "").upper()

        qs = Price.objects.select_related("service").all()

        # Facility-linked pricing
        if getattr(u, "facility_id", None):
            return qs.filter(facility_id=u.facility_id, owner__isnull=True)

        # Independent pricing (LAB/PHARMACY etc.)
        if role in {"LAB", "PHARMACY", "DOCTOR", "NURSE", "FRONTDESK"}:
            return qs.filter(owner=u, facility__isnull=True)

        # System admins can view all
        if role in {"ADMIN", "SUPER_ADMIN"}:
            return qs

        return Price.objects.none()

    def create(self, request, *args, **kwargs):
        # Staff-only price management
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        """Enforce scope on price overrides.

        - Facility-linked users: can only create facility-scoped prices for their facility.
        - Independent users: can only create owner-scoped prices for themselves.
        - ADMIN/SUPER_ADMIN (no facility): may set either scope explicitly.
        """
        u = self.request.user
        role = (getattr(u, "role", "") or "").upper()

        if role in {"ADMIN", "SUPER_ADMIN"} and not getattr(u, "facility_id", None):
            serializer.save()
            return

        if getattr(u, "facility_id", None):
            serializer.save(facility_id=u.facility_id, owner=None)
            return

        serializer.save(owner=u, facility=None)

# --- Charges ---
class ChargeViewSet(viewsets.GenericViewSet, mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin):
    queryset = Charge.objects.select_related("patient","facility","service","created_by")
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create",):
            return ChargeCreateSerializer
        return ChargeReadSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user

        role = (getattr(u, "role", "") or "").upper()
        if role == "PATIENT":
            q = q.filter(patient__user_id=u.id)

        elif getattr(u, "facility_id", None):
            q = q.filter(facility_id=u.facility_id)

        elif role not in {"ADMIN", "SUPER_ADMIN"}:
            # Independent staff should only see their own owner-scoped charges
            q = q.filter(owner=u, facility__isnull=True)

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
            if getattr(charge, 'patient_id', None):
                notify_patient(
                    patient=charge.patient,
                    topic=Topic.BILL_CHARGE_ADDED,
                    priority=Priority.NORMAL,
                    title="New charge added",
                    body=f"{charge.service.name} - {charge.amount}",
                    data={"charge_id": charge.id},
                    facility_id=charge.facility_id or getattr(charge.patient, "facility_id", None),
                    action_url="/patient/billing",
                    group_key=f"BILL:CHARGE:{charge.id}",
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

        role = (getattr(u, "role", "") or "").upper()
        if role == "PATIENT":
            patient_id = u.patient.id if hasattr(u, "patient") and u.patient else None
        if not patient_id:
            return Response({"detail":"patient is required"}, status=400)

        charges = Charge.objects.filter(patient_id=patient_id).exclude(status=ChargeStatus.VOID)
        payments = Payment.objects.filter(patient_id=patient_id)

        # Staff scope to facility or owner
        if role != "PATIENT":
            if getattr(u, "facility_id", None):
                charges = charges.filter(facility_id=u.facility_id)
                payments = payments.filter(facility_id=u.facility_id)
            elif role not in {"ADMIN", "SUPER_ADMIN"}:
                charges = charges.filter(owner=u, facility__isnull=True)
                payments = payments.filter(owner=u, facility__isnull=True)

        ch_total = charges.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        pay_total = payments.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")

        alloc_total = (
            PaymentAllocation.objects.filter(charge__in=charges)
            .aggregate(s=Sum("amount"))["s"]
            or Decimal("0.00")
        )

        balance = Decimal(ch_total) - Decimal(pay_total)
        outstanding = Decimal(ch_total) - Decimal(alloc_total)

        return Response(
            {
                "patient_id": int(patient_id),
                "charges_total": ch_total,
                "payments_total": pay_total,
                "allocated_total": alloc_total,
                "balance": balance,
                "outstanding": outstanding,
            }
        )

# --- Payments ---
class PaymentViewSet(viewsets.GenericViewSet, mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin):
    queryset = Payment.objects.select_related("patient","facility","received_by")
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create",):
            return PaymentCreateSerializer
        return PaymentReadSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user

        role = (getattr(u, "role", "") or "").upper()
        if role == "PATIENT":
            q = q.filter(patient__user_id=u.id)

        elif getattr(u, "facility_id", None):
            q = q.filter(facility_id=u.facility_id)

        elif role not in {"ADMIN", "SUPER_ADMIN"}:
            q = q.filter(owner=u, facility__isnull=True)

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
            if getattr(payment, 'patient_id', None):
                notify_patient(
                    patient=payment.patient,
                    topic=Topic.BILL_PAYMENT_POSTED,
                    priority=Priority.NORMAL,
                    title="Payment received",
                    body=f"Amount: {payment.amount} ({payment.method}) Ref: {payment.reference}",
                    data={"payment_id": payment.id},
                    facility_id=payment.facility_id or getattr(payment.patient, "facility_id", None),
                    action_url="/patient/billing",
                    group_key=f"BILL:PAY:{payment.id}",
                )
        except Exception:
            pass
        return resp
