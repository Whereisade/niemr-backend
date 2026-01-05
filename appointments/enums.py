from django.db import models

class ApptType(models.TextChoices):
    CONSULTATION = "CONSULTATION", "Consultation"
    FOLLOW_UP = "FOLLOW_UP", "Follow-up"
    PROCEDURE = "PROCEDURE", "Procedure"
    DIAGNOSTIC = "DIAGNOSTIC", "Diagnostic (Non-Lab)"
    NURSING_CARE = "NURSING_CARE", "Nursing Care"
    THERAPY = "THERAPY", "Therapy / Rehab"
    MENTAL_HEALTH = "MENTAL_HEALTH", "Mental Health"
    IMMUNIZATION = "IMMUNIZATION", "Immunization"
    MATERNAL_CHILD = "MATERNAL_CHILD", "Maternal / Child Care"
    SURGICAL = "SURGICAL", "Surgical (Pre / Post)"
    EMERGENCY = "EMERGENCY", "Emergency (Non-ER)"
    TELEMEDICINE = "TELEMEDICINE", "Telemedicine"
    HOME_VISIT = "HOME_VISIT", "Home Visit"
    ADMIN_HMO = "ADMIN_HMO", "Administrative / HMO Review"
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