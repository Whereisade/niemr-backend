# providers/views.py
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework import viewsets, mixins, status, permissions, filters
from rest_framework.decorators import action, api_view, permission_classes, authentication_classes, parser_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework_simplejwt.authentication import JWTAuthentication
from .enums import VerificationStatus
from django.utils import timezone
from accounts.enums import UserRole
from accounts.models import User
from accounts.permissions import IsAdmin
# from core.pagination import DefaultPagination
from django.shortcuts import get_object_or_404
from .models import ProviderProfile, ProviderDocument, ProviderFacilityApplication
from .serializers import (
    ProviderProfileSerializer, SelfRegisterProviderSerializer, ProviderDocumentSerializer, ProviderFacilityApplicationSerializer,
    ProviderApplyToFacilitySerializer
)
from .permissions import IsSelfProvider, IsAdmin
from .enums import VerificationStatus


class ProviderViewSet(viewsets.GenericViewSet,
                      mixins.RetrieveModelMixin,
                      mixins.UpdateModelMixin,
                      mixins.ListModelMixin):
    queryset = ProviderProfile.objects.select_related("user").prefetch_related("specialties").all()
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        return ProviderProfileSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user

        # Admins: can see all. Others: can see only approved providers + self (if any)
        if not IsAdmin().has_permission(self.request, self):
            q = q.filter(
                Q(verification_status=VerificationStatus.APPROVED) |
                Q(user_id=u.id)
            )

        # 🔹 NEW: optional facility scoping
        facility_param = self.request.query_params.get("facility")
        if facility_param == "current" and getattr(u, "facility_id", None):
            q = q.filter(user__facility_id=u.facility_id)

        # filters
        state = self.request.query_params.get("state")
        specialty = self.request.query_params.get("specialty")  # name
        ptype = self.request.query_params.get("type")
        status_ = self.request.query_params.get("status")
        s = self.request.query_params.get("s")

        if state:
            q = q.filter(state__iexact=state)
        if specialty:
            q = q.filter(specialties__name__iexact=specialty)
        if ptype:
            q = q.filter(provider_type=ptype)
        if status_:
            q = q.filter(verification_status=status_)
        if s:
            q = q.filter(
                Q(user__first_name__icontains=s) |
                Q(user__last_name__icontains=s) |
                Q(bio__icontains=s)
            )
        return q.distinct().order_by("-created_at", "-id")

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        # Anyone can view APPROVED profiles; owners can view; admins can view
        if obj.verification_status != VerificationStatus.APPROVED:
            if not (request.user.id == obj.user_id or IsAdmin().has_permission(request, self)):
                return Response({"detail": "Not allowed"}, status=403)
        return Response(ProviderProfileSerializer(obj).data)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        # only owner or admin can update
        if not (request.user.id == obj.user_id or IsAdmin().has_permission(request, self)):
            return Response({"detail": "Not allowed"}, status=403)
        return super().update(request, *args, **kwargs)

    # Admin actions
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsAdmin])
    def approve(self, request, pk=None):
        prof = self.get_object()
        prof.approve(request.user)
        return Response({"status": prof.verification_status, "verified_at": prof.verified_at})

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsAdmin])
    def reject(self, request, pk=None):
        prof = self.get_object()
        reason = request.data.get("reason", "")
        prof.reject(request.user, reason)
        return Response({"status": prof.verification_status, "rejection_reason": prof.rejection_reason})

    # Owner uploads docs
    @action(detail=True, methods=["post"])
    def upload(self, request, pk=None):
        prof = self.get_object()
        if request.user.id != prof.user_id and not IsAdmin().has_permission(request, self):
            return Response({"detail": "Not allowed"}, status=403)
        s = ProviderDocumentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        doc = s.save(profile=prof)
        return Response(ProviderDocumentSerializer(doc).data, status=201)


@api_view(["POST"])
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def self_register(request):
    """
    Public: create User (role derived from provider_type) + ProviderProfile (PENDING).
    Accepts multipart/form-data for document uploads.
    """
    s = SelfRegisterProviderSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    result = s.save()

    # Handle both dictionary and ProviderProfile instance returns
    if isinstance(result, dict):
        provider_id = result.get("profile_id") or result.get("provider_id")

        # Get verification status from DB if not in result
        verification_status = result.get("verification_status")
        if provider_id and verification_status is None:
            verification_status = (
                ProviderProfile.objects
                .only("verification_status")
                .get(id=provider_id)
                .verification_status
            )

        response_data = {
            "provider_id": provider_id,
            "verification_status": verification_status,
        }

        # Include tokens if present
        if "access" in result:
            response_data["access"] = result["access"]
            response_data["refresh"] = result["refresh"]

        return Response(response_data, status=201)

    # Handle ProviderProfile instance return
    return Response(
        {"provider_id": result.id, "verification_status": result.verification_status},
        status=201,
    )


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def apply_to_facility(request):
    """
    Providers: submit or update an application to join a Facility.

    POST body:
    { "facility_id": 1, "message": "Optional note" }
    """

    # Only "provider" roles (clinical staff) can apply
    provider_roles = {
        UserRole.DOCTOR,
        UserRole.NURSE,
        UserRole.LAB,
        UserRole.PHARMACY,
    }

    if request.user.role not in provider_roles:
        return Response(
            {"detail": "Only provider accounts can apply to facilities."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = ProviderApplyToFacilitySerializer(
        data=request.data,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    application = serializer.save()  # or pass account if your serializer expects it

    return Response(
        ProviderApplyToFacilitySerializer(application).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([permissions.IsAuthenticated])
def my_facility_applications(request):
    """
    Providers: list all your facility applications (past + current).
    """
    if request.user.role != UserRole.PROVIDER:
        # For non-providers, just return empty list
        return Response([], status=status.HTTP_200_OK)

    try:
        provider = request.user.provider_profile
    except ProviderProfile.DoesNotExist:
        return Response([], status=status.HTTP_200_OK)

    qs = (
        ProviderFacilityApplication.objects.filter(provider=provider)
        .select_related("facility", "provider__user")
        .order_by("-created_at")
    )
    serializer = ProviderFacilityApplicationSerializer(qs, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([permissions.IsAuthenticated, IsAdmin])
def facility_provider_applications(request):
    """
    Facility admin: list provider applications for *your* facility.

    Optional ?status=PENDING|APPROVED|REJECTED
    """
    facility = request.user.facility
    if not facility:
        return Response(
            {"detail": "You are not attached to a facility."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    status_param = request.query_params.get("status")
    qs = ProviderFacilityApplication.objects.filter(facility=facility).select_related(
        "facility", "provider__user"
    )

    if status_param:
        qs = qs.filter(status=status_param.upper())

    serializer = ProviderFacilityApplicationSerializer(qs.order_by("-created_at"), many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([permissions.IsAuthenticated, IsAdmin])
def facility_provider_application_decide(request, pk, decision):
    """
    Facility admin: approve or reject a provider's join application.

    POST /providers/facility/applications/{id}/approve/
    POST /providers/facility/applications/{id}/reject/
    """
    facility = request.user.facility
    if not facility:
        return Response(
            {"detail": "You are not attached to a facility."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    application = get_object_or_404(
        ProviderFacilityApplication.objects.select_related("provider__user", "facility"),
        pk=pk,
        facility=facility,
    )

    if application.status != ProviderFacilityApplication.Status.PENDING:
        return Response(
            {"detail": "This application has already been processed."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    provider_profile = application.provider
    provider_user = provider_profile.user

    if decision == "approve":
        application.status = ProviderFacilityApplication.Status.APPROVED
        # tie provider user to this facility
        provider_user.facility = facility
        provider_user.save(update_fields=["facility"])
        # mark provider as verified/approved
        provider_profile.verification_status = VerificationStatus.APPROVED
        provider_profile.save(update_fields=["verification_status"])
    elif decision == "reject":
        application.status = ProviderFacilityApplication.Status.REJECTED
        provider_profile.verification_status = VerificationStatus.REJECTED
        provider_profile.save(update_fields=["verification_status"])
    else:
        return Response(
            {"detail": "Invalid decision."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    application.decided_at = timezone.now()
    application.decided_by = request.user
    application.save(update_fields=["status", "decided_at", "decided_by"])

    serializer = ProviderFacilityApplicationSerializer(application)
    return Response(serializer.data, status=status.HTTP_200_OK)


