from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from .models import ProviderProfile, ProviderDocument
from .serializers import (
    ProviderProfileSerializer, SelfRegisterProviderSerializer, ProviderDocumentSerializer
)
from .permissions import IsSelfProvider, IsAdmin
from .enums import VerificationStatus

class ProviderViewSet(viewsets.GenericViewSet,
                      mixins.RetrieveModelMixin,
                      mixins.UpdateModelMixin,
                      mixins.ListModelMixin):
    queryset = ProviderProfile.objects.select_related("user").prefetch_related("specialties").all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        return ProviderProfileSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user

        # Admins: can see all. Others: can see only approved providers + self (if any)
        if not IsAdmin().has_permission(self.request, self):
            q = q.filter(Q(verification_status=VerificationStatus.APPROVED) | Q(user_id=u.id))

        # filters
        state = self.request.query_params.get("state")
        specialty = self.request.query_params.get("specialty")  # name
        ptype = self.request.query_params.get("type")
        status_ = self.request.query_params.get("status")
        s = self.request.query_params.get("s")

        if state: q = q.filter(state__iexact=state)
        if specialty: q = q.filter(specialties__name__iexact=specialty)
        if ptype: q = q.filter(provider_type=ptype)
        if status_: q = q.filter(verification_status=status_)
        if s: q = q.filter(Q(user__first_name__icontains=s) | Q(user__last_name__icontains=s) | Q(bio__icontains=s))
        return q.distinct().order_by("-created_at","-id")

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        # Anyone can view APPROVED profiles; owners can view; admins can view
        if obj.verification_status != VerificationStatus.APPROVED:
            if not (request.user.id == obj.user_id or IsAdmin().has_permission(request, self)):
                return Response({"detail":"Not allowed"}, status=403)
        return Response(ProviderProfileSerializer(obj).data)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        # only owner or admin can update
        if not (request.user.id == obj.user_id or IsAdmin().has_permission(request, self)):
            return Response({"detail":"Not allowed"}, status=403)
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
        reason = request.data.get("reason","")
        prof.reject(request.user, reason)
        return Response({"status": prof.verification_status, "rejection_reason": prof.rejection_reason})

    # Owner uploads docs
    @action(detail=True, methods=["post"])
    def upload(self, request, pk=None):
        prof = self.get_object()
        if request.user.id != prof.user_id and not IsAdmin().has_permission(request, self):
            return Response({"detail":"Not allowed"}, status=403)
        s = ProviderDocumentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        doc = s.save(profile=prof)
        return Response(ProviderDocumentSerializer(doc).data, status=201)

@api_view(["POST"])
@permission_classes([AllowAny])
def self_register(request):
    """
    Public: create User (role derived from provider_type) + ProviderProfile (PENDING).
    """
    s = SelfRegisterProviderSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    prof = s.save()
    return Response({"provider_id": prof.id, "verification_status": prof.verification_status}, status=201)
