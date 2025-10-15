from django.db import models

class RxStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PRESCRIBED = "PRESCRIBED", "Prescribed"
    PARTIALLY_DISPENSED = "PARTIALLY_DISPENSED", "Partially Dispensed"
    DISPENSED = "DISPENSED", "Dispensed"
    CANCELLED = "CANCELLED", "Cancelled"

class TxnType(models.TextChoices):
    IN = "IN", "Stock In"
    OUT = "OUT", "Stock Out"
    ADJUST = "ADJUST", "Adjust"
