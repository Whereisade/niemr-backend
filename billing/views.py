import csv
import io
from decimal import Decimal
from datetime import date

from django.db.models import Sum, Q, Count
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_datetime, parse_date
from facilities.permissions_utils import has_facility_permission
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import Service, Price, Charge, Payment, PaymentAllocation
from .serializers import (
    ServiceSerializer,
    PriceSerializer,
    ChargeCreateSerializer,
    ChargeReadSerializer,
    PaymentCreateSerializer,
    PaymentReadSerializer,
    HMOPaymentCreateSerializer,
    HMOOutstandingChargesSerializer,
)
from .permissions import IsStaff
from .enums import ChargeStatus, PaymentMethod, PaymentSource

from notifications.services.notify import notify_patient
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
        """CSV columns: code,name,default_price"""
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
                "default_price": (row.get("default_price") or 0),
                "is_active": True,
            }
            _, is_created = Service.objects.update_or_create(code=code, defaults=defaults)
            created += int(is_created)
            updated += int(not is_created)
        return Response({"created": created, "updated": updated})


# --- Facility/Owner Prices ---
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
class ChargeViewSet(
    viewsets.GenericViewSet,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
):
    queryset = Charge.objects.select_related("patient", "patient__hmo", "facility", "service", "created_by")
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create",):
            return ChargeCreateSerializer
        return ChargeReadSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user

        if not has_facility_permission(u, 'can_view_billing'):
            return q.none()

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
        hmo_id = self.request.query_params.get("hmo")
        status_ = self.request.query_params.get("status")
        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        s = self.request.query_params.get("s")

        if patient_id:
            q = q.filter(patient_id=patient_id)
        if hmo_id:
            q = q.filter(Q(patient__system_hmo_id=hmo_id) | Q(patient__hmo_id=hmo_id))
        if status_:
            q = q.filter(status=status_)
        if start:
            q = q.filter(created_at__gte=parse_datetime(start) or start)
        if end:
            q = q.filter(created_at__lte=parse_datetime(end) or end)
        if s:
            q = q.filter(
                Q(description__icontains=s)
                | Q(service__name__icontains=s)
                | Q(service__code__icontains=s)
            )

        # rollup allocation totals per charge (used in serializers and revenue reports)
        q = q.annotate(allocated_total=Coalesce(Sum("allocations__amount"), Decimal("0.00")))

        return q.order_by("-created_at", "-id")

    # staff-only charge creation
    def create(self, request, *args, **kwargs):
        if not has_facility_permission(request.user, 'can_create_charges'):
            return Response(
                {"detail": "You do not have permission to create charges."},
                status=status.HTTP_403_FORBIDDEN
            )
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        resp = super().create(request, *args, **kwargs)

        # patient notification
        try:
            charge = Charge.objects.select_related("patient", "service").get(id=resp.data["id"])
            if getattr(charge, "patient_id", None):
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
        return Response([c for c, _ in ChargeStatus.choices])

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated, IsStaff])
    def revenue_by_service(self, request):
        """Revenue breakdown for finance dashboards.

        Uses the same scoping rules as the list endpoint.

        Returns:
          - categories: LABS vs PHARMACY vs OTHER
          - services: per service code

        Notes:
          - billed_total uses Charge.amount
          - collected_total uses PaymentAllocation.amount
          - outstanding_total = billed_total - collected_total
        """
        qs = self.get_queryset().exclude(status=ChargeStatus.VOID)

        billed_groups = list(
            qs.values("service_id", "service__code", "service__name")
            .annotate(billed_total=Coalesce(Sum("amount"), Decimal("0.00")), count=Count("id"))
            .order_by("-billed_total")
        )

        collected_groups = (
            PaymentAllocation.objects.filter(charge__in=qs)
            .values("charge__service_id")
            .annotate(collected_total=Coalesce(Sum("amount"), Decimal("0.00")))
        )
        collected_map = {
            row["charge__service_id"]: row["collected_total"] for row in collected_groups
        }

        services = []
        cat_totals = {}

        def classify(code: str) -> str:
            code = (code or "")
            if code.startswith("LAB:"):
                return "LABS"
            if code.startswith("DRUG:"):
                return "PHARMACY"
            return "OTHER"

        for row in billed_groups:
            sid = row["service_id"]
            code = row["service__code"]
            name = row["service__name"]
            billed_total = row["billed_total"] or Decimal("0.00")
            collected_total = collected_map.get(sid, Decimal("0.00"))
            outstanding_total = Decimal(billed_total) - Decimal(collected_total)
            category = classify(code)

            services.append(
                {
                    "service_id": sid,
                    "service_code": code,
                    "service_name": name,
                    "category": category,
                    "count": row["count"],
                    "billed_total": billed_total,
                    "collected_total": collected_total,
                    "outstanding_total": outstanding_total,
                }
            )

            if category not in cat_totals:
                cat_totals[category] = {
                    "category": category,
                    "count": 0,
                    "billed_total": Decimal("0.00"),
                    "collected_total": Decimal("0.00"),
                    "outstanding_total": Decimal("0.00"),
                }
            cat_totals[category]["count"] += row["count"]
            cat_totals[category]["billed_total"] += Decimal(billed_total)
            cat_totals[category]["collected_total"] += Decimal(collected_total)
            cat_totals[category]["outstanding_total"] += Decimal(outstanding_total)

        categories = sorted(
            cat_totals.values(), key=lambda x: (x["category"] != "LABS", x["category"])
        )

        # Optional: trim service list
        top = request.query_params.get("top")
        if top:
            try:
                top_n = max(1, min(100, int(top)))
                services = services[:top_n]
            except Exception:
                pass

        return Response({"categories": categories, "services": services})

    @action(detail=False, methods=["get"])
    def ledger(self, request):
        """Patient ledger view.

        For staff: pass ?patient=<id>
        For patient: implicit to their own.

        Returns totals:
          - charges_total
          - payments_total
          - allocated_total
          - outstanding (charges - allocated)
          - unallocated (payments - allocated)
          - balance (alias of outstanding)
        """
        u = request.user
        patient_id = request.query_params.get("patient")

        role = (getattr(u, "role", "") or "").upper()
        if role == "PATIENT":
            patient_id = (
                u.patient_profile.id
                if hasattr(u, "patient_profile") and u.patient_profile
                else None
            )

        if not patient_id:
            return Response({"detail": "patient is required"}, status=400)

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

        ch_total = charges.aggregate(s=Coalesce(Sum("amount"), Decimal("0.00")))["s"]
        pay_total = payments.aggregate(s=Coalesce(Sum("amount"), Decimal("0.00")))["s"]

        alloc_total = (
            PaymentAllocation.objects.filter(charge__in=charges)
            .aggregate(s=Coalesce(Sum("amount"), Decimal("0.00")))["s"]
        )

        outstanding = Decimal(ch_total) - Decimal(alloc_total)
        unallocated = Decimal(pay_total) - Decimal(alloc_total)

        return Response(
            {
                "patient_id": int(patient_id),
                "charges_total": ch_total,
                "payments_total": pay_total,
                "allocated_total": alloc_total,
                "outstanding": outstanding,
                "unallocated": unallocated,
                "balance": outstanding,
            }
        )

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated, IsStaff], url_path="hmo-outstanding")
    def hmo_outstanding(self, request):
        """
        Get outstanding charges for an HMO.
        
        Query params:
          - hmo: HMO ID (required)
          - start: Start date filter (optional)
          - end: End date filter (optional)
        
        Returns summary of charges and detailed charge list.
        """
        u = request.user
        facility_id = getattr(u, "facility_id", None)
        
        if not facility_id:
            return Response(
                {"detail": "User must belong to a facility"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        hmo_id = request.query_params.get("hmo")
        if not hmo_id:
            return Response(
                {"detail": "hmo parameter is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Base queryset: charges for patients under this HMO
        charges_qs = (
            Charge.objects
            .select_related("patient", "service")
            .filter(
                facility_id=facility_id,
                patient__hmo_id=hmo_id,
            )
            .exclude(status=ChargeStatus.VOID)
        )
        
        # Date filters
        start = request.query_params.get("start")
        end = request.query_params.get("end")
        if start:
            start_date = parse_date(start) or parse_datetime(start)
            if start_date:
                charges_qs = charges_qs.filter(created_at__gte=start_date)
        if end:
            end_date = parse_date(end) or parse_datetime(end)
            if end_date:
                charges_qs = charges_qs.filter(created_at__lte=end_date)
        
        # Annotate with allocated amounts
        charges_qs = charges_qs.annotate(
            allocated_total=Coalesce(Sum("allocations__amount"), Decimal("0.00"))
        )
        
        # Calculate summary
        summary = charges_qs.aggregate(
            total_charges=Coalesce(Sum("amount"), Decimal("0.00")),
            total_paid=Coalesce(Sum("allocations__amount"), Decimal("0.00")),
            patient_count=Count("patient_id", distinct=True),
            charge_count=Count("id"),
        )
        
        total_outstanding = Decimal(summary["total_charges"]) - Decimal(summary["total_paid"])
        
        # Prepare detailed charges
        charges_data = []
        for charge in charges_qs[:100]:  # Limit to 100 for performance
            outstanding = Decimal(str(charge.amount)) - Decimal(str(charge.allocated_total))
            if outstanding > 0:  # Only include unpaid/partially paid
                charges_data.append({
                    "id": charge.id,
                    "patient_id": charge.patient_id,
                    "patient_name": f"{charge.patient.first_name} {charge.patient.last_name}",
                    "service_code": charge.service.code,
                    "service_name": charge.service.name,
                    "description": charge.description,
                    "amount": charge.amount,
                    "allocated": charge.allocated_total,
                    "outstanding": outstanding,
                    "status": charge.status,
                    "created_at": charge.created_at,
                })
        
        return Response({
            "summary": {
                "total_charges": summary["total_charges"],
                "total_paid": summary["total_paid"],
                "total_outstanding": total_outstanding,
                "patient_count": summary["patient_count"],
                "charge_count": summary["charge_count"],
            },
            "charges": charges_data,
        })


# --- Payments ---
class PaymentViewSet(
    viewsets.GenericViewSet,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
):
    queryset = Payment.objects.select_related("patient", "hmo", "system_hmo", "facility_hmo", "facility", "received_by")
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create",):
            return PaymentCreateSerializer
        elif self.action in ("record_hmo_payment",):
            return HMOPaymentCreateSerializer
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

        # Filters
        patient_id = self.request.query_params.get("patient")
        hmo_id = self.request.query_params.get("hmo")
        payment_source = self.request.query_params.get("payment_source")
        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        s = self.request.query_params.get("s")
        method = self.request.query_params.get("method")

        if patient_id:
            q = q.filter(patient_id=patient_id)
        if hmo_id:
            q = q.filter(Q(facility_hmo_id=hmo_id) | Q(system_hmo_id=hmo_id) | Q(hmo_id=hmo_id))
        if payment_source:
            q = q.filter(payment_source=payment_source)
        if method:
            q = q.filter(method=method)
        if start:
            q = q.filter(received_at__gte=parse_datetime(start) or start)
        if end:
            q = q.filter(received_at__lte=parse_datetime(end) or end)
        if s:
            q = q.filter(Q(reference__icontains=s) | Q(note__icontains=s))

        return q.order_by("-received_at", "-id")

    # staff-only
    def create(self, request, *args, **kwargs):
        if not has_facility_permission(request.user, 'can_manage_payments'):
            return Response(
                {"detail": "You do not have permission to manage payments."},
                status=status.HTTP_403_FORBIDDEN
            )
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        resp = super().create(request, *args, **kwargs)

        # patient notification
        try:
            payment = Payment.objects.select_related("patient").get(id=resp.data["id"])
            if getattr(payment, "patient_id", None):
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

    @action(detail=False, methods=["get"])
    def methods(self, request):
        return Response([m for m, _ in PaymentMethod.choices])
    
    @action(detail=False, methods=["get"])
    def sources(self, request):
        """Return available payment sources"""
        return Response([s for s, _ in PaymentSource.choices])

    @action(
        detail=False, 
        methods=["post"], 
        permission_classes=[IsAuthenticated, IsStaff],
        url_path="hmo-payment"
    )
    def record_hmo_payment(self, request):
        """
        Record a bulk payment from an HMO.
        
        POST /api/billing/payments/hmo-payment/
        
        Body:
        {
            "hmo_id": 123,
            "amount": "50000.00",
            "method": "TRANSFER",
            "reference": "HMO-2025-001",
            "note": "January 2025 settlement",
            "period_start": "2025-01-01",
            "period_end": "2025-01-31",
            "allocations": [  // Optional - manual allocations
                {"charge_id": 1, "amount": "5000.00"},
                {"charge_id": 2, "amount": "3000.00"}
            ],
            "auto_allocate": true  // If true and no allocations, auto-allocate to oldest charges
        }
        
        Returns the created payment with allocations.
        """
        if not has_facility_permission(request.user, 'can_manage_payments'):
            return Response(
                {"detail": "You do not have permission to manage payments."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = serializer.save()
        
        # Return full payment details
        output_serializer = PaymentReadSerializer(payment)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)