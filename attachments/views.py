from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404
from django.utils.encoding import smart_str
from django.db.models import Q
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from patients.models import Patient
from .models import File, AttachmentLink
from .serializers import FileSerializer, UploadSerializer, LinkSerializer
from .permissions import CanViewFile, IsStaff
from .enums import Visibility


# Map generic ref types from the frontend to concrete app/model pairs.
REF_TYPE_MAP = {
    # Encounters
    "ENCOUNTER": ("encounters", "encounter"),
    "ENCOUNTER_AMENDMENT": ("encounters", "encounteramendment"),

    # Labs
    "LAB": ("labs", "laborder"),
    "LAB_ORDER": ("labs", "laborder"),

    # Imaging
    "IMAGING": ("imaging", "imagingrequest"),
    "IMAGING_REQUEST": ("imaging", "imagingrequest"),

    # Pharmacy
    "PRESCRIPTION": ("pharmacy", "prescription"),
}


def _normalize_ref_type(raw):
    """
    Normalise various frontend ref types (e.g. 'lab_order', 'LAB', 'imaging')
    into the canonical keys used in REF_TYPE_MAP.
    """
    if not raw:
        return None
    t = str(raw).strip().upper()

    # Friendly aliases
    if t in {"LAB_ORDER", "LABORDER"}:
        return "LAB_ORDER"
    if t in {"LAB"}:
        return "LAB"
    if t in {"IMAGING_REQUEST", "IMAGINGREQUEST"}:
        return "IMAGING_REQUEST"
    if t in {"IMAGING"}:
        return "IMAGING"
    if t in {"ENCOUNTER", "ENCOUNTERS", "VISIT"}:
        return "ENCOUNTER"
    if t in {"ENCOUNTER_AMENDMENT", "ENCOUNTERAMENDMENT", "SOAP_CORRECTION", "SOAP_AMENDMENT", "AMENDMENT"}:
        return "ENCOUNTER_AMENDMENT"
    if t in {"PRESCRIPTION", "RX"}:
        return "PRESCRIPTION"

    return t


def _resolve_content_type_and_id(ref_type, ref_id):
    """
    Turn ref_type + ref_id into (ContentType, object_id), or (None, None)
    if we can't resolve it.
    """
    ref_type = _normalize_ref_type(ref_type)
    if not (ref_type and ref_id):
        return None, None

    mapping = REF_TYPE_MAP.get(ref_type)
    if mapping:
        app_label, model = mapping
    else:
        # Allow sending "app_label.model" explicitly as ref_type if you ever need it.
        lower = str(ref_type).lower()
        if "." in lower:
            app_label, model = lower.split(".", 1)
        else:
            return None, None

    try:
        ct = ContentType.objects.get(app_label=app_label, model=model)
    except ContentType.DoesNotExist:
        return None, None

    try:
        obj_id = int(ref_id)
    except (TypeError, ValueError):
        return None, None

    return ct, obj_id


def _get_patient_id_from_object(obj):
    """Best-effort extraction of patient_id from an arbitrary model instance."""
    if not obj:
        return None
    pid = getattr(obj, "patient_id", None)
    if pid:
        return pid
    p = getattr(obj, "patient", None)
    if p is None:
        return None
    # patient FK might be an object or an integer
    if isinstance(p, int):
        return p
    return getattr(p, "id", None)


def _get_patient_for_ref(ct, obj_id):
    """
    Try to resolve a Patient instance for a referenced object.

    We keep this generic to avoid importing feature apps (labs/imaging/etc.)
    while still supporting patient-scoped attachment access.
    """
    try:
        model = ct.model_class() if ct else None
    except Exception:
        model = None

    if not model:
        return None

    try:
        obj = model.objects.filter(pk=obj_id).first()
    except Exception:
        return None

    if not obj:
        return None

    patient_id = _get_patient_id_from_object(obj)
    if not patient_id:
        return None

    return Patient.objects.filter(id=patient_id).select_related("user").first()


class FileViewSet(
    viewsets.GenericViewSet,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
):
    queryset = File.objects.select_related("patient", "facility", "uploaded_by").all()
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        return FileSerializer

    def get_queryset(self):
        u = self.request.user
        q = self.queryset

        params = self.request.query_params

        # 🔗 Object-scoped filters (labs, imaging, encounters, etc.)
        ref_type = params.get("ref_type") or params.get("owner_type")
        ref_id = params.get("ref_id") or params.get("owner_id")

        # Compatibility parameters used in some frontend code
        lab_order = params.get("lab_order")
        imaging_request = params.get("imaging_request")

        if not (ref_type and ref_id) and lab_order:
            ref_type, ref_id = "LAB_ORDER", lab_order
        if not (ref_type and ref_id) and imaging_request:
            ref_type, ref_id = "IMAGING_REQUEST", imaging_request

        ct, obj_id = (None, None)
        if ref_type and ref_id:
            ct, obj_id = _resolve_content_type_and_id(ref_type, ref_id)

        role = (getattr(u, "role", "") or "").upper()

        # Scope by user
        if role == "PATIENT":
            # Important: Some attachments are linked to the lab order but do NOT have file.patient set.
            # If the patient is requesting object-scoped attachments, validate ownership on the
            # referenced object and then list linked files.
            if ct and obj_id:
                p = _get_patient_for_ref(ct, obj_id)
                if not p or p.user_id != getattr(u, "id", None):
                    return q.none()

                # Show linked files for this object. Exclude INTERNAL by default.
                q = (
                    q.filter(links__content_type=ct, links__object_id=obj_id)
                    .exclude(visibility=Visibility.INTERNAL)
                    .distinct()
                )

                # If files have patient set, ensure they match the patient; otherwise allow null.
                q = q.filter(Q(patient_id=p.id) | Q(patient__isnull=True))
            else:
                q = q.filter(patient__user_id=u.id)

        elif getattr(u, "facility_id", None):
            q = q.filter(facility_id=u.facility_id)

        else:
            # Independent staff users (no facility) must NOT see all files.
            # Scope to files they uploaded or that belong to patients related to them.
            role = (getattr(u, "role", "") or "").upper()
            if role not in {"SUPER_ADMIN", "ADMIN"}:
                uid = getattr(u, "id", None)
                if uid:
                    q = q.filter(
                        Q(uploaded_by_id=uid)
                        | Q(patient__appointments__provider_id=uid)
                        | Q(patient__encounters__created_by_id=uid)
                        | Q(patient__encounters__provider_id=uid)
                        | Q(patient__encounters__nurse_id=uid)
                        | Q(patient__lab_orders__ordered_by_id=uid)
                        | Q(patient__lab_orders__outsourced_to_id=uid)
                        | Q(patient__prescriptions__prescribed_by_id=uid)
                        | Q(patient__prescriptions__outsourced_to_id=uid)
                    ).distinct()

        # Simple filters
        patient = params.get("patient")
        tag = params.get("tag")
        vis = params.get("visibility")

        if patient:
            q = q.filter(patient_id=patient)
        if tag:
            q = q.filter(tag__iexact=tag)
        if vis:
            q = q.filter(visibility=vis)

        # Apply object-scoped filters for non-patient (or patient without special handling)
        if ref_type and ref_id and not (role == "PATIENT" and ct and obj_id):
            if ct and obj_id:
                q = q.filter(links__content_type=ct, links__object_id=obj_id).distinct()

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
        - description: optional

        Optional linking fields (any of these, used by the frontend):
        - ref_type + ref_id            (e.g. ENCOUNTER, LAB, IMAGING, PRESCRIPTION)
        - owner_type + owner_id        (e.g. "lab_order", "imaging_request")
        - lab_order                    (numeric id)
        - imaging_request              (numeric id)
        """
        s = UploadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data

        # Associate with patient/facility if provided
        patient = None
        if data.get("patient"):
            patient = get_object_or_404(Patient, id=data["patient"])

        # If patient isn't provided, try to infer it from the referenced object.
        # This fixes cases where lab staff attach files to a LabOrder but don't set file.patient.
        raw = request.data  # QueryDict from DRF

        ref_type = (
            raw.get("ref_type")
            or raw.get("owner_type")
            or ("LAB_ORDER" if raw.get("lab_order") else None)
            or ("IMAGING_REQUEST" if raw.get("imaging_request") else None)
        )
        ref_id = (
            raw.get("ref_id")
            or raw.get("owner_id")
            or raw.get("lab_order")
            or raw.get("imaging_request")
        )

        ct, obj_id = _resolve_content_type_and_id(ref_type, ref_id)
        if not patient and ct and obj_id:
            patient = _get_patient_for_ref(ct, obj_id)

        f = File.objects.create(
            file=data["file"],
            original_name=data["file"].name,
            mime_type=getattr(data["file"], "content_type", ""),
            uploaded_by=request.user,
            facility=(
                getattr(request.user, "facility", None)
                or (patient.facility if patient else None)
            ),
            patient=patient,
            visibility=data.get("visibility"),
            tag=data.get("tag", ""),
            description=data.get("description", ""),
        )

        # 🔗 Optional: link this file to a specific object
        if ct and obj_id:
            AttachmentLink.objects.get_or_create(
                file=f,
                content_type=ct,
                object_id=obj_id,
            )

        return Response(FileSerializer(f).data, status=status.HTTP_201_CREATED)

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
        if getattr(u, "role", None) == "PATIENT":
            if not (f.patient and f.patient.user_id == u.id):
                return Response({"detail": "Not allowed"}, status=403)
        elif u.facility_id != f.facility_id and u.role not in (
            "SUPER_ADMIN",
            "ADMIN",
        ):
            return Response({"detail": "Not allowed"}, status=403)
        if f.visibility == "INTERNAL" and u.role not in (
            "SUPER_ADMIN",
            "ADMIN",
        ):
            return Response(
                {"detail": "Admins only for INTERNAL files"}, status=403
            )

        f.file.delete(save=False)  # remove binary
        f.delete()
        return Response(status=204)
