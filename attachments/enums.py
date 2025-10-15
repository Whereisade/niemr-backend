from django.db import models

class Visibility(models.TextChoices):
    PRIVATE = "PRIVATE", "Private (staff only, facility-scoped)"
    PATIENT = "PATIENT", "Visible to the patient (and staff)"
    INTERNAL = "INTERNAL", "Internal (admins only)"
