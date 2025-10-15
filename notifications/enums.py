from django.db import models

class Channel(models.TextChoices):
    IN_APP = "IN_APP", "In-App"
    EMAIL  = "EMAIL", "Email"

class Topic(models.TextChoices):
    LAB_RESULT_READY     = "LAB_RESULT_READY", "Lab result ready"
    IMAGING_REPORT_READY = "IMAGING_REPORT_READY", "Imaging report ready"
    APPT_REMINDER        = "APPT_REMINDER", "Appointment reminder"
    BILL_CHARGE_ADDED    = "BILL_CHARGE_ADDED", "New charge added"
    BILL_PAYMENT_POSTED  = "BILL_PAYMENT_POSTED", "Payment posted"
    GENERAL              = "GENERAL", "General"
