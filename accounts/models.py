from django.contrib.auth.models import AbstractUser
from django.db import models
from .enums import UserRole

class User(AbstractUser):
    # Use email as the login field (username kept for admin convenience)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=UserRole.choices, default=UserRole.PATIENT)
    email_verified = models.BooleanField(default=False)
    facility = models.ForeignKey("facilities.Facility", null=True, blank=True, on_delete=models.SET_NULL)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]  # to keep createsuperuser flow simple

    def __str__(self):
        return f"{self.email} ({self.role})"
