# appointments/management/commands/seed_appointment_services_simple.py
"""
SIMPLE VERSION: Creates services with ₦1.00 placeholder prices.
Facilities MUST override these with real prices.

This works with your current database structure without any migrations needed.
"""

from django.core.management.base import BaseCommand
from decimal import Decimal
from billing.models import Service


class Command(BaseCommand):
    help = "Create appointment services with placeholder prices (facilities must override)"

    def handle(self, *args, **options):
        services = [
            ("APPT:CONSULTATION", "Consultation"),
            ("APPT:FOLLOW_UP", "Follow-up Visit"),
            ("APPT:PROCEDURE", "Procedure"),
            ("APPT:DIAGNOSTIC_NON_LAB", "Diagnostic (Non-Lab)"),
            ("APPT:NURSING_CARE", "Nursing Care"),
            ("APPT:THERAPY_REHAB", "Therapy/Rehabilitation"),
            ("APPT:MENTAL_HEALTH", "Mental Health"),
            ("APPT:IMMUNIZATION", "Immunization/Vaccination"),
            ("APPT:MATERNAL_CHILD_CARE", "Maternal/Child Care"),
            ("APPT:SURGICAL_PRE_POST", "Surgical (Pre/Post-op)"),
            ("APPT:EMERGENCY_NON_ER", "Emergency (Non-ER)"),
            ("APPT:TELEMEDICINE", "Telemedicine"),
            ("APPT:HOME_VISIT", "Home Visit"),
            ("APPT:ADMIN_HMO_REVIEW", "Administrative/HMO Review"),
            ("APPT:LAB", "Lab Visit"),
            ("APPT:IMAGING", "Imaging Visit"),
            ("APPT:PHARMACY", "Pharmacy Pickup"),
            ("APPT:OTHER", "Other"),
        ]
        
        self.stdout.write("\nCreating appointment services with ₦1.00 placeholders...\n")
        
        created = 0
        updated = 0
        
        for code, name in services:
            # Only set fields that definitely exist
            defaults = {
                "name": name,
                "default_price": Decimal("1.00"),  # Placeholder
            }
            
            # Try to add description if field exists
            try:
                Service._meta.get_field('description')
                defaults["description"] = f"{name} appointment"
            except:
                pass
            
            service, is_new = Service.objects.update_or_create(
                code=code,
                defaults=defaults
            )
            
            if is_new:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"✅ {name}"))
            else:
                updated += 1
                self.stdout.write(self.style.WARNING(f"📝 {name} (updated)"))
        
        self.stdout.write(f"\n✅ Created {created}, Updated {updated}")
        self.stdout.write(self.style.WARNING(
            "\n⚠️  IMPORTANT: Prices are set to ₦1.00 placeholders"
        ))
        self.stdout.write(self.style.WARNING(
            "⚠️  Facilities MUST set real prices via Pricing Management page\n"
        ))