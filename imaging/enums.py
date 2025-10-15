from django.db import models

class Modality(models.TextChoices):
    XR  = "XR",  "X-Ray"
    US  = "US",  "Ultrasound"
    CT  = "CT",  "CT"
    MRI = "MRI", "MRI"

class RequestStatus(models.TextChoices):
    REQUESTED  = "REQUESTED","Requested"
    SCHEDULED  = "SCHEDULED","Scheduled"
    REPORTED   = "REPORTED","Reported"
    CANCELLED  = "CANCELLED","Cancelled"

class Priority(models.TextChoices):
    ROUTINE="ROUTINE","Routine"
    URGENT="URGENT","Urgent"
    STAT="STAT","Stat"
