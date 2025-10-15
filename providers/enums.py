from django.db import models

class ProviderType(models.TextChoices):
    DOCTOR = "DOCTOR","Medical Doctor"
    NURSE = "NURSE","Nurse"
    PHARMACIST = "PHARMACIST","Pharmacist"
    LAB_SCIENTIST = "LAB_SCIENTIST","Medical Lab Scientist"
    DENTIST = "DENTIST","Dentist"
    OPTOMETRIST = "OPTOMETRIST","Optometrist"
    PHYSIOTHERAPIST = "PHYSIOTHERAPIST","Physiotherapist"
    OTHER = "OTHER","Other"

class Council(models.TextChoices):
    MDCN = "MDCN","Medical & Dental Council"
    NMCN = "NMCN","Nursing & Midwifery Council"
    PCN  = "PCN","Pharmacists Council"
    MLSCN= "MLSCN","Med. Lab. Science Council"
    RADI = "RADI","Radiographers Board"
    OTHER= "OTHER","Other"

class VerificationStatus(models.TextChoices):
    PENDING = "PENDING","Pending"
    APPROVED = "APPROVED","Approved"
    REJECTED = "REJECTED","Rejected"
