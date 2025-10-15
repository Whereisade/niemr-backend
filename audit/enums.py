from django.db import models

class Verb(models.TextChoices):
    CREATE = "CREATE", "Create"
    UPDATE = "UPDATE", "Update"
    DELETE = "DELETE", "Delete"
    M2M    = "M2M",    "Many-to-Many Change"
    LOGIN  = "LOGIN",  "Login"
    LOGOUT = "LOGOUT", "Logout"
    ACTION = "ACTION", "Custom Action"
