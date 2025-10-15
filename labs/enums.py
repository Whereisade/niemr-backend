from django.db import models

class OrderStatus(models.TextChoices):
    PENDING = "PENDING","Pending"
    IN_PROGRESS = "IN_PROGRESS","In Progress"
    COMPLETED = "COMPLETED","Completed"
    CANCELLED = "CANCELLED","Cancelled"

class Priority(models.TextChoices):
    ROUTINE="ROUTINE","Routine"
    URGENT="URGENT","Urgent"
    STAT="STAT","Stat"

class Flag(models.TextChoices):
    NORMAL = "NORMAL","Normal"
    LOW    = "LOW","Low"
    HIGH   = "HIGH","High"
    CRIT   = "CRIT","Critical"
