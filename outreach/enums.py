from django.db import models

class OutreachStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ACTIVE = "ACTIVE", "Active"
    CLOSED = "CLOSED", "Closed"

class Sex(models.TextChoices):
    MALE = "MALE", "Male"
    FEMALE = "FEMALE", "Female"
    OTHER = "OTHER", "Other"
    UNKNOWN = "UNKNOWN", "Unknown"

class LabOrderStatus(models.TextChoices):
    ORDERED = "ORDERED", "Ordered"
    COLLECTED = "COLLECTED", "Collected"
    RESULT_READY = "RESULT_READY", "Result Ready"

class CounselingVisibility(models.TextChoices):
    PRIVATE = "PRIVATE", "Private (counselor + OSA)"
    INTERNAL = "INTERNAL", "Internal (staff with permission)"

class BloodEligibilityStatus(models.TextChoices):
    ELIGIBLE = "ELIGIBLE", "Eligible"
    NOT_ELIGIBLE = "NOT_ELIGIBLE", "Not eligible"

class BloodDonationOutcome(models.TextChoices):
    COMPLETED = "COMPLETED", "Completed"
    DEFERRED = "DEFERRED", "Deferred"

class PregnancyStatus(models.TextChoices):
    PREGNANT = "PREGNANT", "Pregnant"
    NOT_PREGNANT = "NOT_PREGNANT", "Not pregnant"
    UNKNOWN = "UNKNOWN", "Unknown"
