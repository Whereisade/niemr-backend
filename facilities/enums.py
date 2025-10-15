from django.db import models

class FacilityType(models.TextChoices):
    HOSPITAL = "HOSPITAL","Hospital"
    CLINIC = "CLINIC","Clinic"
    EYE_CLINIC = "EYE_CLINIC","Eye Clinic"
    DENTAL_CLINIC = "DENTAL_CLINIC","Dental Clinic"
    IMAGING_CENTER = "IMAGING_CENTER","Imaging & Diagnostic Center"
    LABORATORY = "LABORATORY","Laboratory"
    SOLO_PRACTICE = "SOLO_PRACTICE","Solo Practice"
    AMBULATORY_CENTER = "AMBULATORY_CENTER","Ambulatory Surgical Center"
    OTHER = "OTHER","Other"
