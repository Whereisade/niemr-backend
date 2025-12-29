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