from django.db import transaction
from django.utils import timezone

from rest_framework import viewsets, mixins, status
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts.models import User
from accounts.enums import UserRole
from .models import (
    Facility,
    Specialty,
    Ward,
    Bed,
    FacilityExtraDocument,
    BedAssignment,
    refresh_bed_status,
)
from .serializers import (
    FacilityCreateSerializer,
    FacilityDetailSerializer,
    SpecialtySerializer,
    WardSerializer,
    BedSerializer,
    FacilityExtraDocumentSerializer,
    FacilityAdminSignupSerializer,
    BedAssignmentSerializer,
)
from .permissions import IsFacilityAdmin


class FacilityViewSet(
    viewsets.GenericViewSet,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
):
    queryset = Facility.objects.all().order_by("-created_at")
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return FacilityCreateSerializer
        return FacilityDetailSerializer

    @transaction.atomic
    def perform_create(self, serializer):
        """
        Facility Super Admin registration flow:
        - Create facility
        - If creator has no facility, link them & elevate to SUPER_ADMIN
        """
        facility = serializer.save()
        user = self.request.user
        if not user.facility:
            user.facility = facility
        # If a patient or no role, promote to SUPER_ADMIN (Hospital SuperAdmin)
        if user.role in (UserRole.PATIENT,) or not user.role:
            user.role = UserRole.SUPER_ADMIN
        user.save()

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, IsFacilityAdmin],
    )
    def upload_extra(self, request, pk=None):
        facility = self.get_object()
        s = FacilityExtraDocumentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.save(facility=facility)
        return Response(s.data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, IsFacilityAdmin],
    )
    def add_ward(self, request, pk=None):
        facility = self.get_object()
        s = WardSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.save(facility=facility)
        return Response(s.data, status=201)

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, IsFacilityAdmin],
    )
    def add_bed(self, request, pk=None):
        """
        Add one bed or multiple (if payload contains list under 'items').
        """
        facility = self.get_object()
        ward_id = request.data.get("ward")
        if not ward_id:
            return Response({"detail": "ward is required"}, status=400)
        try:
            ward = facility.wards.get(id=ward_id)
        except Ward.DoesNotExist:
            return Response(
                {"detail": "Ward not found for this facility"}, status=404
            )

        items = request.data.get("items")
        if items and isinstance(items, list):
            created = []
            for item in items:
                s = BedSerializer(data={"ward": ward.id, **item})
                s.is_valid(raise_exception=True)
                s.save()
                created.append(s.data)
            return Response({"created": created}, status=201)

        s = BedSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data, status=201)

    @action(
        detail=True,
        methods=["get"],
        url_path="ward-summary",
        permission_classes=[IsAuthenticated],
    )
    def ward_summary(self, request, pk=None):
        """
        Returns a compact summary of wards and bed usage for this facility.
        """
        facility = self.get_object()
        wards = facility.wards.prefetch_related("beds").order_by("name")

        summary = []
        for w in wards:
            beds = list(w.beds.all())
            bed_count = len(beds)

            # Using the new status field
            occupied = sum(
                1 for b in beds if b.status == Bed.BedStatus.OCCUPIED
            )
            available = sum(
                1
                for b in beds
                if b.status == Bed.BedStatus.AVAILABLE and b.is_operational
            )

            summary.append(
                {
                    "id": w.id,
                    "name": w.name,
                    "ward_type": w.ward_type,
                    "ward_type_display": w.get_ward_type_display(),
                    "gender_policy": w.gender_policy,
                    "gender_policy_display": w.get_gender_policy_display(),
                    "floor": w.floor,
                    "capacity": w.capacity,
                    "bed_count": bed_count,
                    "occupied_beds": occupied,
                    "available_beds": available,
                }
            )

        return Response(summary)

    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
    def specialties(self, request):
        qs = Specialty.objects.all().order_by("name")
        return Response(SpecialtySerializer(qs, many=True).data)

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[IsAuthenticated, IsFacilityAdmin],
    )
    def seed_specialties(self, request):
        """
        Seed your provided specialty list from the doc (idempotent).
        """
        names = request.data.get("names") or []
        created = []
        for n in names:
            obj, was_created = Specialty.objects.get_or_create(name=n.strip())
            if was_created:
                created.append(obj.name)
        return Response({"created": created}, status=201)

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, IsFacilityAdmin],
    )
    def assign_user(self, request, pk=None):
        """
        Assign an existing user to this facility and set role.
        payload: { "user_id": "...", "role": "DOCTOR" }
        """
        facility = self.get_object()
        user_id = request.data.get("user_id")
        role = request.data.get("role")
        if not (user_id and role):
            return Response(
                {"detail": "user_id and role are required"}, status=400
            )
        try:
            u = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found"}, status=404)
        u.facility = facility
        u.role = role
        u.save()
        return Response({"ok": True})


class BedAssignmentViewSet(viewsets.ModelViewSet):
    """
    Manage bed assignments (bed ← patient) within a facility.
    """
    serializer_class = BedAssignmentSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = BedAssignment.objects.select_related(
            "bed",
            "bed__ward",
            "bed__ward__facility",
            "patient",
            "encounter",
        )

        user = self.request.user
        if getattr(user, "facility_id", None):
            qs = qs.filter(bed__ward__facility_id=user.facility_id)

        bed_id = self.request.query_params.get("bed")
        ward_id = self.request.query_params.get("ward")
        patient_id = self.request.query_params.get("patient")
        active = self.request.query_params.get("active")

        if bed_id:
            qs = qs.filter(bed_id=bed_id)
        if ward_id:
            qs = qs.filter(bed__ward_id=ward_id)
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if active in ("true", "True", "1"):
            qs = qs.filter(discharged_at__isnull=True)

        return qs.order_by("-assigned_at")

    def perform_create(self, serializer):
        serializer.save()

    @action(detail=True, methods=["post"])
    def discharge(self, request, pk=None):
        """
        Mark this bed assignment as discharged (free the bed).
        """
        assignment = self.get_object()
        if assignment.discharged_at is not None:
            return Response(
                {"detail": "Assignment is already discharged."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        assignment.discharged_at = timezone.now()
        user = request.user
        if user and user.is_authenticated:
            assignment.discharged_by = user
        assignment.save()

        serializer = self.get_serializer(assignment)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        """
        Move an active bed assignment to a different bed (within same facility).
        Body: {"bed": <new_bed_id>}
        """
        assignment = self.get_object()
        if assignment.discharged_at is not None:
            return Response(
                {"detail": "Cannot transfer a discharged assignment."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_bed_id = request.data.get("bed")
        try:
            new_bed_id = int(new_bed_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Valid 'bed' id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            new_bed = Bed.objects.select_related(
                "ward", "ward__facility"
            ).get(pk=new_bed_id)
        except Bed.DoesNotExist:
            return Response(
                {"detail": "New bed not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        user = request.user
        # Facility scoping
        if getattr(user, "facility_id", None):
            if new_bed.ward.facility_id != user.facility_id:
                return Response(
                    {"detail": "You can only transfer within your facility."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Ensure no active assignment on new bed
        if new_bed.assignments.filter(discharged_at__isnull=True).exists():
            return Response(
                {"detail": "Target bed already has an active assignment."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_bed = assignment.bed
        assignment.bed = new_bed
        assignment.save()

        # Refresh both beds
        refresh_bed_status(old_bed)
        refresh_bed_status(new_bed)

        serializer = self.get_serializer(assignment)
        return Response(serializer.data)


# --- NIEMR: Public endpoint to create Facility + Super Admin and return tokens ---
class FacilityAdminRegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, *args, **kwargs):
        s = FacilityAdminSignupSerializer(
            data=request.data, context={"request": request}
        )
        s.is_valid(raise_exception=True)
        payload = s.save()  # dict with facility, user, tokens
        return Response(payload, status=status.HTTP_201_CREATED)


