from django.db import models

class Channel(models.TextChoices):
    IN_APP = "IN_APP", "In-App"
    EMAIL  = "EMAIL", "Email"


class Priority(models.TextChoices):
    LOW = "LOW", "Low"
    NORMAL = "NORMAL", "Normal"
    HIGH = "HIGH", "High"
    URGENT = "URGENT", "Urgent"

class Topic(models.TextChoices):
    # Clinical
    LAB_RESULT_READY     = "LAB_RESULT_READY", "Lab result ready"
    LAB_RESULT_CRITICAL  = "LAB_RESULT_CRITICAL", "Critical lab result"
    IMAGING_REPORT_READY = "IMAGING_REPORT_READY", "Imaging report ready"
    PRESCRIPTION_READY   = "PRESCRIPTION_READY", "Prescription ready"
    PRESCRIPTION_REFILL  = "PRESCRIPTION_REFILL", "Prescription refill reminder"

    # Appointments
    APPT_REMINDER           = "APPT_REMINDER", "Appointment reminder"
    APPOINTMENT_REMINDER    = "APPOINTMENT_REMINDER", "Appointment reminder"
    APPOINTMENT_CONFIRMED   = "APPOINTMENT_CONFIRMED", "Appointment confirmed"
    APPOINTMENT_CHECKED_IN  = "APPOINTMENT_CHECKED_IN", "Appointment checked-in"
    APPOINTMENT_COMPLETED   = "APPOINTMENT_COMPLETED", "Appointment completed"
    APPOINTMENT_CANCELLED   = "APPOINTMENT_CANCELLED", "Appointment cancelled"
    APPOINTMENT_NO_SHOW     = "APPOINTMENT_NO_SHOW", "Appointment no-show"
    APPOINTMENT_RESCHEDULED = "APPOINTMENT_RESCHEDULED", "Appointment rescheduled"

    # Encounters
    ENCOUNTER_CREATED   = "ENCOUNTER_CREATED", "Encounter created"
    ENCOUNTER_UPDATED   = "ENCOUNTER_UPDATED", "Encounter updated"
    ENCOUNTER_COMPLETED = "ENCOUNTER_COMPLETED", "Encounter completed"

    # Billing
    BILL_CHARGE_ADDED    = "BILL_CHARGE_ADDED", "New charge added"
    BILL_PAYMENT_POSTED  = "BILL_PAYMENT_POSTED", "Payment posted"
    BILLING              = "BILLING", "Billing"
    PAYMENT_DUE          = "PAYMENT_DUE", "Payment due"
    PAYMENT_RECEIVED     = "PAYMENT_RECEIVED", "Payment received"

    # Operations
    STAFF_ASSIGNED        = "STAFF_ASSIGNED", "Staff assigned"
    WARD_ADMISSION_REQUEST = "WARD_ADMISSION_REQUEST", "Ward admission request"
    MESSAGE               = "MESSAGE", "Message"
    REMINDER              = "REMINDER", "Reminder"
    VITAL_ALERT           = "VITAL_ALERT", "Vital alert"
    ALLERGY_ALERT         = "ALLERGY_ALERT", "Allergy alert"

    # System
    SYSTEM_ANNOUNCEMENT = "SYSTEM_ANNOUNCEMENT", "System announcement"
    SYSTEM_MAINTENANCE  = "SYSTEM_MAINTENANCE", "System maintenance"
    ACCOUNT             = "ACCOUNT", "Account"
    GENERAL             = "GENERAL", "General"
