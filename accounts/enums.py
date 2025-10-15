from django.db import models

class UserRole(models.TextChoices):
    SUPER_ADMIN = "SUPER_ADMIN", "Super Admin"
    ADMIN       = "ADMIN", "Admin"
    DOCTOR      = "DOCTOR", "Doctor"
    NURSE       = "NURSE", "Nurse"
    LAB         = "LAB", "Lab Scientist"
    PHARMACY    = "PHARMACY", "Pharmacy"
    FRONTDESK   = "FRONTDESK", "Front Desk"
    PATIENT     = "PATIENT", "Patient"
