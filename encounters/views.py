from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from accounts.models import User
from appointments.models import Appointment
from appointments.enums import ApptStatus
from patients.models import Patient
from accounts.enums import UserRole
from .enums import EncounterStage, EncounterStatus, SoapSection
from .models import Encounter, EncounterAmendment
from .permissions import CanViewEncounter, IsStaff
from .serializers import AmendmentSerializer, EncounterListSerializer, EncounterSerializer


class EncounterViewSet(
    viewsets.GenericViewSet,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
):
    queryset = Encounter.objects.select_related("patient", "facility", "created_by", "nurse",  "provider").all()
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        return EncounterListSerializer if self.action == "list" else EncounterSerializer

    def get_queryset(self):
        q = self.queryset
        u = self.request.user

        if u.role == "PATIENT":
            q = q.filter(patient__user_id=u.id)
        elif u.facility_id:
            q = q.filter(facility_id=u.facility_id)
        else:
            role = (getattr(u, "role", "") or "").upper()
            if role not in {"ADMIN", "SUPER_ADMIN"}:
                q = q.filter(created_by_id=u.id)

        patient_id = self.request.query_params.get("patient")
        if patient_id:
            q = q.filter(patient_id=patient_id)

        status_ = self.request.query_params.get("status")
        if status_:
            q = q.filter(status=status_)

        stage_ = self.request.query_params.get("stage")
        if stage_:
            q = q.filter(stage=stage_)

        s = self.request.query_params.get("s")
        if s:
            q = q.filter(
                Q(chief_complaint__icontains=s)
                | Q(diagnoses__icontains=s)
                | Q(plan__icontains=s)
            )

        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        if start:
            q = q.filter(occurred_at__gte=parse_datetime(start) or start)
        if end:
            q = q.filter(occurred_at__lte=parse_datetime(end) or end)

        return q

    def create(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewEncounter]
        self.check_object_permissions(request, obj)
        return Response(EncounterSerializer(obj, context={"request": request}).data)

    def update(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)
        return super().update(request, *args, **kwargs)

    # ─────────────────────────────────────────────────────────────
    # Start flows
    # ─────────────────────────────────────────────────────────────

    @action(detail=False, methods=["post"], url_path="start-from-appointment")
    def start_from_appointment(self, request):
        """
        Start an encounter from an appointment.
        - Creates encounter if none exists
        - Links encounter to appointment
        - Updates appointment status to CHECKED_IN
        - Sets nurse or provider based on user role
        """
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        appt_id = request.data.get("appointment_id")
        if not appt_id:
            return Response({"detail": "appointment_id is required"}, status=400)

        appt = Appointment.objects.select_related("patient", "facility").filter(id=appt_id).first()
        if not appt:
            return Response({"detail": "Appointment not found"}, status=404)

        if request.user.facility_id and appt.facility_id != request.user.facility_id:
            return Response({"detail": "Appointment is not in your facility"}, status=403)

        if appt.status in (ApptStatus.CANCELLED, ApptStatus.COMPLETED, ApptStatus.NO_SHOW):
            return Response(
                {
                    "detail": f"Cannot start encounter for {appt.status} appointment.",
                    "appointment_status": appt.status,
                },
                status=400,
            )

        # Reuse existing active encounter if present
        if appt.encounter_id:
            enc = Encounter.objects.filter(id=appt.encounter_id).first()
            if enc and enc.status not in (EncounterStatus.CLOSED, EncounterStatus.CROSSED_OUT):
                return Response(EncounterSerializer(enc, context={"request": request}).data)

        # Create new encounter with proper role assignment
        user_role = getattr(request.user, "role", "").upper()
        
        # Determine who is starting the encounter
        if user_role == UserRole.NURSE:
            # Nurse starts: set nurse, leave provider empty
            enc = Encounter.objects.create(
                patient=appt.patient,
                facility=appt.facility,
                created_by=request.user,
                nurse=request.user,
                provider=None,  # Will be set when doctor takes over
                status=EncounterStatus.IN_PROGRESS,
                stage=EncounterStage.LABS,  # Actually starts at nurse assessment, but kept for compatibility
                occurred_at=timezone.now(),
                appointment_id=appt.id,
                chief_complaint=getattr(appt, "reason", "") or "",
            )
        elif user_role in (UserRole.DOCTOR, UserRole.ADMIN, UserRole.SUPER_ADMIN):
            # Doctor starts directly: set provider, no nurse
            enc = Encounter.objects.create(
                patient=appt.patient,
                facility=appt.facility,
                created_by=request.user,
                nurse=None,
                provider=request.user,
                status=EncounterStatus.IN_PROGRESS,
                stage=EncounterStage.LABS,
                occurred_at=timezone.now(),
                appointment_id=appt.id,
                chief_complaint=getattr(appt, "reason", "") or "",
            )
        else:
            # Other roles: just set created_by
            enc = Encounter.objects.create(
                patient=appt.patient,
                facility=appt.facility,
                created_by=request.user,
                status=EncounterStatus.IN_PROGRESS,
                stage=EncounterStage.LABS,
                occurred_at=timezone.now(),
                appointment_id=appt.id,
                chief_complaint=getattr(appt, "reason", "") or "",
            )

        # Link encounter to appointment
        appt.encounter_id = enc.id

        # Update appointment status to CHECKED_IN if still SCHEDULED
        if appt.status == ApptStatus.SCHEDULED:
            appt.status = ApptStatus.CHECKED_IN

        appt.save(update_fields=["encounter_id", "status", "updated_at"])

        return Response(EncounterSerializer(enc, context={"request": request}).data, status=201)


    @action(detail=False, methods=["post"], url_path="start-from-patient")
    def start_from_patient(self, request):
        """
        Start a walk-in encounter directly from patient (no appointment).
        Sets nurse or provider based on user role.
        """
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        patient_id = request.data.get("patient_id")
        if not patient_id:
            return Response({"detail": "patient_id is required"}, status=400)

        patient = Patient.objects.select_related("facility").filter(id=patient_id).first()
        if not patient:
            return Response({"detail": "Patient not found"}, status=404)

        if request.user.facility_id and patient.facility_id != request.user.facility_id:
            return Response({"detail": "Patient is not in your facility"}, status=403)

        user_role = getattr(request.user, "role", "").upper()
        
        # Determine who is starting the encounter
        if user_role == UserRole.NURSE:
            enc = Encounter.objects.create(
                patient=patient,
                facility=request.user.facility if request.user.facility_id else patient.facility,
                created_by=request.user,
                nurse=request.user,
                provider=None,
                status=EncounterStatus.IN_PROGRESS,
                stage=EncounterStage.LABS,
                occurred_at=timezone.now(),
            )
        elif user_role in (UserRole.DOCTOR, UserRole.ADMIN, UserRole.SUPER_ADMIN):
            enc = Encounter.objects.create(
                patient=patient,
                facility=request.user.facility if request.user.facility_id else patient.facility,
                created_by=request.user,
                nurse=None,
                provider=request.user,
                status=EncounterStatus.IN_PROGRESS,
                stage=EncounterStage.LABS,
                occurred_at=timezone.now(),
            )
        else:
            enc = Encounter.objects.create(
                patient=patient,
                facility=request.user.facility if request.user.facility_id else patient.facility,
                created_by=request.user,
                status=EncounterStatus.IN_PROGRESS,
                stage=EncounterStage.LABS,
                occurred_at=timezone.now(),
            )
        
        return Response(EncounterSerializer(enc, context={"request": request}).data, status=201)

    @action(detail=True, methods=["post"], url_path="assign_provider")
    def assign_provider(self, request, pk=None):
        """
        Assign a doctor as the provider for this encounter.
        Can be called by nurses to assign a doctor, or by doctors to assign themselves.
        """
        import traceback
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            enc = self.get_object()
            self.permission_classes = [IsAuthenticated, IsStaff]
            self.check_permissions(request)

            # Get the provider ID from request body (if provided)
            provider_id = request.data.get("provider")
            logger.info(f"assign_provider called with provider_id={provider_id}, user={request.user.id}")
            
            if provider_id:
                # Nurse is assigning a specific doctor
                try:
                    logger.info(f"Attempting to fetch User with id={provider_id}")
                    
                    # Check if User model is importable
                    from accounts.models import User as UserModel
                    logger.info(f"User model imported successfully: {UserModel}")
                    
                    provider = UserModel.objects.get(id=provider_id)
                    logger.info(f"Found provider: {provider.id}, {provider.email}, role={provider.role}")
                    
                    # Verify the provider being assigned is a doctor/admin
                    provider_role = getattr(provider, "role", "")
                    logger.info(f"Provider role: '{provider_role}' (type: {type(provider_role)})")
                    
                    # Import UserRole to compare
                    from accounts.enums import UserRole as UR
                    logger.info(f"UserRole values: DOCTOR={UR.DOCTOR}, ADMIN={UR.ADMIN}, SUPER_ADMIN={UR.SUPER_ADMIN}")
                    
                    if provider_role not in (UR.DOCTOR, UR.ADMIN, UR.SUPER_ADMIN):
                        logger.warning(f"Provider role '{provider_role}' not in allowed roles")
                        return Response(
                            {"detail": f"Only doctors can be assigned as providers. Provider role is: {provider_role}"},
                            status=400,
                        )
                    
                    # Verify provider is in same facility (if applicable)
                    if request.user.facility_id:
                        logger.info(f"Checking facility: user.facility_id={request.user.facility_id}, provider.facility_id={provider.facility_id}")
                        if provider.facility_id != request.user.facility_id:
                            return Response(
                                {"detail": "Can only assign providers from your facility."},
                                status=403,
                            )
                    
                    enc.provider = provider
                    logger.info(f"Provider assigned successfully")
                    
                except UserModel.DoesNotExist:
                    logger.error(f"User with id={provider_id} not found")
                    return Response(
                        {"detail": "Provider not found."},
                        status=404,
                    )
                except Exception as e:
                    logger.error(f"Error in provider assignment: {str(e)}")
                    logger.error(traceback.format_exc())
                    return Response(
                        {"detail": f"Error assigning provider: {str(e)}"},
                        status=400,
                    )
            else:
                # Doctor is assigning themselves
                user_role = getattr(request.user, "role", "")
                logger.info(f"Self-assignment by user {request.user.id}, role={user_role}")
                
                from accounts.enums import UserRole as UR
                if user_role not in (UR.DOCTOR, UR.ADMIN, UR.SUPER_ADMIN):
                    return Response(
                        {"detail": "Only doctors or admins can assign themselves as providers."},
                        status=403,
                    )
                
                enc.provider = request.user
                logger.info(f"Self-assigned successfully")

            enc.save(update_fields=["provider", "updated_at"])


            # Keep the appointment record in sync so the facility appointment list
            # reflects the doctor assigned during the encounter workflow.
            try:
                if enc.appointment_id and enc.provider_id:
                    from appointments.models import Appointment

                    appt = Appointment.objects.filter(id=enc.appointment_id).first()
                    if appt and appt.provider_id != enc.provider_id:
                        appt.provider_id = enc.provider_id
                        appt.save(update_fields=["provider", "updated_at"])
            except Exception:
                # Never fail provider assignment because appointment sync failed
                pass
            logger.info(f"Encounter {enc.id} saved with provider {enc.provider_id}")

            serializer = EncounterSerializer(enc, context={"request": request})
            logger.info("Serializer created, returning response")
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"Unexpected error in assign_provider: {str(e)}")
            logger.error(traceback.format_exc())
            return Response(
                {"detail": f"Unexpected error: {str(e)}"},
                status=500,
            )
    # ─────────────────────────────────────────────────────────────
    # Lab wait controls
    # ─────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        """Pause encounter while waiting for labs."""
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        enc.status = EncounterStatus.WAITING_LABS
        enc.stage = EncounterStage.WAITING_LABS
        enc.paused_at = timezone.now()
        enc.paused_by = request.user
        enc.save(update_fields=["status", "stage", "paused_at", "paused_by", "updated_at"])
        return Response(EncounterSerializer(enc, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        """Resume a paused encounter."""
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        enc.status = EncounterStatus.IN_PROGRESS
        enc.stage = EncounterStage.NOTE
        enc.resumed_at = timezone.now()
        enc.resumed_by = request.user
        enc.save(update_fields=["status", "stage", "resumed_at", "resumed_by", "updated_at"])
        return Response(EncounterSerializer(enc, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="skip_labs")
    def skip_labs(self, request, pk=None):
        """Skip labs and go directly to SOAP note."""
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        enc.labs_skipped_at = timezone.now()
        enc.labs_skipped_by = request.user
        enc.status = EncounterStatus.IN_PROGRESS
        enc.stage = EncounterStage.NOTE
        enc.save(
            update_fields=[
                "labs_skipped_at",
                "labs_skipped_by",
                "status",
                "stage",
                "updated_at",
            ]
        )
        return Response(EncounterSerializer(enc, context={"request": request}).data)

    # ─────────────────────────────────────────────────────────────
    # Clinical actions
    # ─────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """
        Close the encounter.
        Also updates linked appointment status to COMPLETED.
        """
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        enc.status = EncounterStatus.CLOSED
        enc.save(update_fields=["status", "updated_at"])

        # Sync linked appointment to COMPLETED
        if enc.appointment_id:
            try:
                appt = Appointment.objects.filter(id=enc.appointment_id).first()
                if appt and appt.status not in (
                    ApptStatus.COMPLETED,
                    ApptStatus.CANCELLED,
                    ApptStatus.NO_SHOW,
                ):
                    appt.status = ApptStatus.COMPLETED
                    appt.save(update_fields=["status", "updated_at"])
            except Exception:
                pass

        return Response(
            {
                "detail": "Encounter closed.",
                "status": enc.status,
            }
        )

    @action(detail=True, methods=["post"])
    def cross_out(self, request, pk=None):
        """
        Cross-out is disabled. Use per-section corrections via /amend/ instead.
        """
        return Response(
            {
                "detail": (
                    "Encounter cross-out is disabled. "
                    "After a note is locked, add a correction to a specific SOAP section via "
                    "POST /api/encounters/{id}/amend/ with {section, reason, content}."
                )
            },
            status=400,
        )

    @action(detail=True, methods=["post"])
    def amend(self, request, pk=None):
        """Add a per-section correction to a locked encounter."""
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        enc.maybe_lock()
        if not enc.is_locked:
            return Response(
                {
                    "detail": "Encounter note is not locked yet. Edit the SOAP fields directly until the lock activates.",
                    "lock_due_at": getattr(enc, "lock_due_at", None),
                },
                status=400,
            )

        section = (request.data.get("section") or "").strip()
        if not section:
            return Response({"detail": "section is required"}, status=400)
        if section not in {c[0] for c in SoapSection.choices}:
            return Response({"detail": "Invalid section"}, status=400)

        reason = (request.data.get("reason") or "").strip()
        content = (request.data.get("content") or "").strip()
        if not reason or not content:
            return Response({"detail": "reason and content are required"}, status=400)

        a = EncounterAmendment.objects.create(
            encounter=enc,
            added_by=request.user,
            section=section,
            reason=reason,
            content=content,
        )

        return Response(
            AmendmentSerializer(a, context={"request": request, "attachments_map": {}}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"])
    def amendments(self, request, pk=None):
        """List amendments (corrections) for an encounter."""
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, CanViewEncounter]
        self.check_object_permissions(request, enc)

        qs = enc.amendments.select_related("added_by").order_by("created_at")

        section = (request.query_params.get("section") or "").strip()
        if section:
            qs = qs.filter(section=section)

        attachments_map = {}
        amendment_ids = list(qs.values_list("id", flat=True))
        if amendment_ids:
            try:
                from django.contrib.contenttypes.models import ContentType
                from attachments.models import AttachmentLink
                from attachments.serializers import FileSerializer

                ct = ContentType.objects.get(app_label="encounters", model="encounteramendment")
                links = (
                    AttachmentLink.objects.filter(content_type=ct, object_id__in=amendment_ids)
                    .select_related("file")
                    .order_by("id")
                )
                for link in links:
                    attachments_map.setdefault(link.object_id, []).append(
                        FileSerializer(link.file, context={"request": request}).data
                    )
            except Exception:
                attachments_map = {}

        return Response(
            AmendmentSerializer(
                qs,
                many=True,
                context={"request": request, "attachments_map": attachments_map},
            ).data
        )

    @action(detail=True, methods=["post"], url_path="finalize_note")
    def finalize_note(self, request, pk=None):
        """
        Marks the SOAP/Dx part as completed and starts the 24h lock timer.
        Moves workflow to PRESCRIPTION stage.
        """
        enc = self.get_object()
        self.permission_classes = [IsAuthenticated, IsStaff]
        self.check_permissions(request)

        if not enc.clinical_finalized_at:
            enc.clinical_finalized_at = timezone.now()
            enc.clinical_finalized_by = request.user

        enc.stage = EncounterStage.PRESCRIPTION
        enc.save(update_fields=["clinical_finalized_at", "clinical_finalized_by", "stage", "updated_at"])
        return Response(EncounterSerializer(enc, context={"request": request}).data)
