from django.db import models


class EncounterStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    WAITING_LABS = "WAITING_LABS", "Waiting (Labs)"
    CLOSED = "CLOSED", "Closed"
    CROSSED_OUT = "CROSSED_OUT", "Crossed Out"


class EncounterStage(models.TextChoices):
    """
    Workflow stage for multi-step encounter UI:
    - TRIAGE: nurse vitals (optional in UI; vitals module already exists)
    - LABS: lab ordering step (catalog + manual test request + outsource)
    - WAITING_LABS: read-only while awaiting results
    - NOTE: SOAP note / diagnosis
    - PRESCRIPTION: medication entry (catalog + free-text + outsource)
    """
    TRIAGE = "TRIAGE", "Triage"
    LABS = "LABS", "Labs"
    WAITING_LABS = "WAITING_LABS", "Waiting (Labs)"
    NOTE = "NOTE", "SOAP Note"
    PRESCRIPTION = "PRESCRIPTION", "Prescription"


class EncounterType(models.TextChoices):
    NEW = "NEW", "New"
    FOLLOW_UP = "FOLLOW_UP", "Follow-up"
    VIRTUAL = "VIRTUAL", "Virtual"


class Priority(models.TextChoices):
    ROUTINE = "ROUTINE", "Routine"
    URGENT = "URGENT", "Urgent"
    STAT = "STAT", "Stat"
