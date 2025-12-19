from django.db import models

class PatientStatus(models.TextChoices):
    OUTPATIENT = "OUTPATIENT","Outpatient"
    INPATIENT  = "INPATIENT","Inpatient"
    DISCHARGED = "DISCHARGED","Discharged"

class EncounterType(models.TextChoices):
    NEW       = "NEW","New"
    FOLLOW_UP = "FOLLOW_UP","Follow-up"
    VIRTUAL   = "VIRTUAL","Virtual"

class BloodGroup(models.TextChoices):
    O_POS="O+","O+"
    O_NEG="O-","O-"
    A_POS="A+","A+"
    A_NEG="A-","A-"
    B_POS="B+","B+"
    B_NEG="B-","B-"
    AB_POS="AB+","AB+"
    AB_NEG="AB-","AB-"
    OTHER="OTHER","Other"

class Genotype(models.TextChoices):
    AA="AA","AA"
    AS="AS","AS"
    SC="SC","SC"
    AC="AC","AC"
    OTHER="OTHER","Other"

class InsuranceStatus(models.TextChoices):
    SELF_PAY="SELF_PAY","Self pay"
    INSURED="INSURED","Insured"

class AllergyType(models.TextChoices):
    DRUG = "DRUG", "Drug / Medication"
    FOOD = "FOOD", "Food"
    ENVIRONMENTAL = "ENVIRONMENTAL", "Environmental"
    INSECT = "INSECT", "Insect"
    LATEX = "LATEX", "Latex"
    OTHER = "OTHER", "Other"

class AllergySeverity(models.TextChoices):
    MILD = "MILD", "Mild"
    MODERATE = "MODERATE", "Moderate"
    SEVERE = "SEVERE", "Severe"
    LIFE_THREATENING = "LIFE_THREATENING", "Life-threatening"