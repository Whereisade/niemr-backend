from django.db import transaction
from rest_framework import viewsets, mixins, status
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from accounts.models import User
from accounts.enums import UserRole
from .models import Facility, Specialty, Ward, Bed, FacilityExtraDocument
from .serializers import (
    FacilityCreateSerializer, FacilityDetailSerializer,
    SpecialtySerializer, WardSerializer, BedSerializer,
    FacilityExtraDocumentSerializer,
    FacilityAdminSignupSerializer
)
from .permissions import IsFacilityAdmin

class FacilityViewSet(viewsets.GenericViewSet,
                      mixins.CreateModelMixin,
                      mixins.RetrieveModelMixin,
                      mixins.UpdateModelMixin,
                      mixins.ListModelMixin):
    queryset = Facility.objects.all().order_by("-created_at")
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create","update","partial_update"):
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

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsFacilityAdmin])
    def upload_extra(self, request, pk=None):
        facility = self.get_object()
        s = FacilityExtraDocumentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.save(facility=facility)
        return Response(s.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsFacilityAdmin])
    def add_ward(self, request, pk=None):
        facility = self.get_object()
        s = WardSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.save(facility=facility)
        return Response(s.data, status=201)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsFacilityAdmin])
    def add_bed(self, request, pk=None):
        """
        Add one bed or multiple (if payload contains list under 'items').
        """
        facility = self.get_object()
        ward_id = request.data.get("ward")
        if not ward_id:
            return Response({"detail":"ward is required"}, status=400)
        try:
            ward = facility.wards.get(id=ward_id)
        except Ward.DoesNotExist:
            return Response({"detail":"Ward not found for this facility"}, status=404)

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

    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
    def specialties(self, request):
        qs = Specialty.objects.all().order_by("name")
        return Response(SpecialtySerializer(qs, many=True).data)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsFacilityAdmin])
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

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsFacilityAdmin])
    def assign_user(self, request, pk=None):
        """
        Assign an existing user to this facility and set role.
        payload: { "user_id": "...", "role": "DOCTOR" }
        """
        facility = self.get_object()
        user_id = request.data.get("user_id")
        role = request.data.get("role")
        if not (user_id and role):
            return Response({"detail":"user_id and role are required"}, status=400)
        try:
            u = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail":"User not found"}, status=404)
        u.facility = facility
        u.role = role
        u.save()
        return Response({"ok": True})


# --- NIEMR: Public endpoint to create Facility + Super Admin and return tokens ---
class FacilityAdminRegisterView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, *args, **kwargs):
        s = FacilityAdminSignupSerializer(data=request.data, context={"request": request})
        s.is_valid(raise_exception=True)
        payload = s.save()  # dict with facility, user, tokens
        return Response(payload, status=status.HTTP_201_CREATED)
