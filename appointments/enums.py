from django.db import models

class ApptType(models.TextChoices):
    CONSULT = "CONSULT", "Consultation"
    FOLLOW_UP = "FOLLOW_UP", "Follow-up"
    LAB = "LAB", "Lab Visit"
    IMAGING = "IMAGING", "Imaging Visit"
    PHARMACY = "PHARMACY", "Pharmacy Pickup"
    OTHER = "OTHER", "Other"

class ApptStatus(models.TextChoices):
    SCHEDULED = "SCHEDULED","Scheduled"
    CHECKED_IN = "CHECKED_IN","Checked-in"
    COMPLETED = "COMPLETED","Completed"
    CANCELLED = "CANCELLED","Cancelled"
    NO_SHOW = "NO_SHOW","No-show"
