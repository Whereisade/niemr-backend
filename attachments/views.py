from django.shortcuts import get_object_or_404
from django.utils.encoding import smart_str
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from patients.models import Patient
from .models import File, AttachmentLink
from .serializers import FileSerializer, UploadSerializer, LinkSerializer
from .permissions import CanViewFile, IsStaff
from .enums import Visibility

class FileViewSet(viewsets.GenericViewSet,
                  mixins.RetrieveModelMixin,
                  mixins.ListModelMixin):
    queryset = File.objects.select_related("patient","facility","uploaded_by").all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        return FileSerializer

    def get_queryset(self):
        u = self.request.user
        q = self.queryset

        # Patients see only their own files
        if u.role == "PATIENT":
            q = q.filter(patient__user_id=u.id)
        elif u.facility_id:
            # Staff see their facility files
            q = q.filter(facility_id=u.facility_id)

        # filters
        patient = self.request.query_params.get("patient")
        tag = self.request.query_params.get("tag")
        vis = self.request.query_params.get("visibility")
        if patient: q = q.filter(patient_id=patient)
        if tag: q = q.filter(tag__iexact=tag)
        if vis: q = q.filter(visibility=vis)

        return q

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewFile]
        self.check_object_permissions(request, obj)
        return Response(FileSerializer(obj).data)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def upload(self, request):
        """
        Multipart/form-data:
        - file: <binary>
        - patient: <id> (optional; associates file for patient and auto-infers facility)
        - visibility: PRIVATE/PATIENT/INTERNAL
        - tag: optional
        """
        s = UploadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        patient = None
        if data.get("patient"):
            patient = get_object_or_404(Patient, id=data["patient"])

        f = File.objects.create(
            file=data["file"],
            original_name=data["file"].name,
            mime_type=getattr(data["file"], "content_type", ""),
            uploaded_by=request.user,
            facility=request.user.facility or (patient.facility if patient else None),
            patient=patient,
            visibility=data.get("visibility"),
            tag=data.get("tag",""),
        )
        return Response(FileSerializer(f).data, status=201)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsStaff])
    def link(self, request):
        """
        Link an existing file to any object.
        payload: { file_id, app_label, model, object_id }
        e.g., { "file_id": 10, "app_label": "imaging", "model": "imagingreport", "object_id": 55 }
        """
        s = LinkSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        link = s.save()
        return Response({"linked": True, "file_id": link.file_id})

    @action(detail=True, methods=["delete"], permission_classes=[IsAuthenticated])
    def delete(self, request, pk=None):
        """
        Delete a file:
        - Patient may delete their own PATIENT-visibility files.
        - Staff may delete files in their facility (except INTERNAL without admin).
        """
        f = self.get_object()
        u = request.user
        # authz
        if u.role == "PATIENT":
            if not (f.patient and f.patient.user_id == u.id):
                return Response({"detail":"Not allowed"}, status=403)
        elif u.facility_id != f.facility_id and u.role not in ("SUPER_ADMIN","ADMIN"):
            return Response({"detail":"Not allowed"}, status=403)
        if f.visibility == "INTERNAL" and u.role not in ("SUPER_ADMIN","ADMIN"):
            return Response({"detail":"Admins only for INTERNAL files"}, status=403)

        f.file.delete(save=False)  # remove binary
        f.delete()
        return Response(status=204)
