from django.db import models

class ChargeStatus(models.TextChoices):
    UNPAID = "UNPAID","Unpaid"
    PARTIALLY_PAID = "PARTIALLY_PAID","Partially Paid"
    PAID = "PAID","Paid"
    VOID = "VOID","Void"

class PaymentMethod(models.TextChoices):
    CASH = "CASH","Cash"
    CARD = "CARD","Card"
    POS  = "POS","POS"
    TRANSFER = "TRANSFER","Bank Transfer"
    CHEQUE = "CHEQUE","Cheque"
    INSURANCE = "INSURANCE","Insurance"
    OTHER = "OTHER","Other"

class PaymentSource(models.TextChoices):
    """
    Source of payment - who is paying the facility.
    """
    PATIENT_DIRECT = "PATIENT_DIRECT", "Patient Direct Payment"
    HMO = "HMO", "HMO Payment"
    INSURANCE = "INSURANCE", "Insurance Payment"
    CORPORATE = "CORPORATE", "Corporate Payment"
    OTHER = "OTHER", "Other"