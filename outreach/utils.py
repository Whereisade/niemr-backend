from __future__ import annotations

import re
import secrets
import string
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from accounts.enums import UserRole
from .models import OutreachEvent, OutreachAuditLog

def normalize_username_to_email(username_or_email: str) -> str:
    s = (username_or_email or "").strip()
    if not s:
        return ""
    if "@" in s:
        return s.lower()
    base = re.sub(r"[^a-zA-Z0-9._-]", "", s).lower() or "staff"
    return f"{base}@outreach.local"

def generate_password(length: int = 12) -> str:
    alphabet = (string.ascii_letters + string.digits).replace("O", "").replace("0", "").replace("l", "")
    return "".join(secrets.choice(alphabet) for _ in range(max(10, length)))

def log_action(event: OutreachEvent, actor, action: str, meta: dict | None = None):
    OutreachAuditLog.objects.create(outreach_event=event, actor=actor, action=action, meta=meta or {})

@transaction.atomic
def allocate_next_patient_code(event: OutreachEvent) -> str:
    evt = OutreachEvent.objects.select_for_update().get(pk=event.pk)
    evt.patient_seq = (evt.patient_seq or 0) + 1
    evt.save(update_fields=["patient_seq"])
    return f"OUT-{evt.patient_seq:06d}"

def create_or_reset_outreach_user(*, email: str, full_name: str = "", password: str | None = None, role: str = UserRole.ADMIN):
    """Create or reset a Django auth user used for outreach staff.

    NOTE: Users are created with facility=None.
    Password is returned (only show once to the OSA UI).
    """
    User = get_user_model()
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("email is required")

    parts = [p for p in str(full_name).strip().split() if p]
    first_name = parts[0] if parts else ""
    last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    if password is None:
        password = generate_password()

    user = User.objects.filter(email=email).first()
    created = False
    if user:
        user.first_name = first_name or user.first_name
        user.last_name = last_name or user.last_name
        user.role = getattr(UserRole, role, user.role)
        user.facility = None
        user.is_active = True
        user.set_password(password)
        user.save(update_fields=["first_name", "last_name", "role", "facility", "is_active", "password"])
    else:
        user = User.objects.create_user(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role=getattr(UserRole, role, UserRole.ADMIN),
            facility=None,
        )
        created = True
    return user, created, password
