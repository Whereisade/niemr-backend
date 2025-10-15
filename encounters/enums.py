from django.db import models

class EncounterStatus(models.TextChoices):
    OPEN     = "OPEN", "Open"
    CLOSED   = "CLOSED", "Closed"

class EncounterType(models.TextChoices):
    NEW       = "NEW","New"
    FOLLOW_UP = "FOLLOW_UP","Follow-up"
    VIRTUAL   = "VIRTUAL","Virtual"

class Priority(models.TextChoices):
    ROUTINE = "ROUTINE","Routine"
    URGENT  = "URGENT","Urgent"
    STAT    = "STAT","Stat"
