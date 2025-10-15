from django.db import models

class SeverityFlag(models.TextChoices):
    GREEN  = "GREEN",  "Green"   # normal
    YELLOW = "YELLOW", "Yellow"  # borderline / attention
    RED    = "RED",    "Red"     # abnormal / critical
