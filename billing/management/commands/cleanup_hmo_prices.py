# billing/management/commands/cleanup_hmo_prices.py
"""
Management command to clean up HMOPrice data during SystemHMO migration.

This command helps transition from the old facility-scoped HMO model
to the new SystemHMO model.

Options:
1. Delete all HMOPrice records (clean slate)
2. List existing HMOPrice records for review
3. Attempt to migrate existing prices to SystemHMO (if possible)
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from billing.models import HMOPrice
from patients.models import SystemHMO, HMO


class Command(BaseCommand):
    help = 'Cleanup HMOPrice data during SystemHMO migration'

    def add_arguments(self, parser):
        parser.add_argument(
            '--action',
            type=str,
            choices=['list', 'delete', 'migrate'],
            default='list',
            help='Action to perform: list (show existing), delete (remove all), migrate (attempt conversion)'
        )
        
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip confirmation prompts'
        )

    def handle(self, *args, **options):
        action = options['action']
        force = options['force']
        
        if action == 'list':
            self.list_hmo_prices()
        elif action == 'delete':
            self.delete_hmo_prices(force)
        elif action == 'migrate':
            self.migrate_hmo_prices(force)

    def list_hmo_prices(self):
        """List all existing HMOPrice records."""
        prices = HMOPrice.objects.select_related(
            'facility', 'owner', 'service', 'tier'
        ).all()
        
        count = prices.count()
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('✅ No HMOPrice records found.'))
            return
        
        self.stdout.write(f'\n📊 Found {count} HMOPrice record(s):\n')
        
        for price in prices[:50]:  # Limit to first 50
            scope = f"Facility: {price.facility.name}" if price.facility else f"Provider: {price.owner_id}"
            hmo = f"SystemHMO: {price.system_hmo.name}" if price.system_hmo else "SystemHMO: NONE"
            tier = f" | Tier: {price.tier.name}" if price.tier else ""
            
            self.stdout.write(
                f"  ID {price.id}: {scope} | {hmo}{tier} | "
                f"{price.service.code} = {price.amount}"
            )
        
        if count > 50:
            self.stdout.write(f'\n  ... and {count - 50} more records')
        
        self.stdout.write('\n')

    def delete_hmo_prices(self, force=False):
        """Delete all HMOPrice records."""
        count = HMOPrice.objects.count()
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('✅ No HMOPrice records to delete.'))
            return
        
        self.stdout.write(self.style.WARNING(f'\n⚠️  About to delete {count} HMOPrice record(s).'))
        
        if not force:
            confirm = input('Are you sure you want to continue? [y/N]: ')
            if confirm.lower() != 'y':
                self.stdout.write(self.style.ERROR('❌ Aborted.'))
                return
        
        with transaction.atomic():
            deleted_count, _ = HMOPrice.objects.all().delete()
        
        self.stdout.write(self.style.SUCCESS(f'✅ Deleted {deleted_count} HMOPrice record(s).'))

    def migrate_hmo_prices(self, force=False):
        """
        Attempt to migrate HMOPrice records to SystemHMO.
        
        This is a best-effort migration that tries to:
        1. Find or create matching SystemHMOs for facility HMOs
        2. Update HMOPrice records to reference SystemHMO
        
        Note: This may not work perfectly if your old HMO data doesn't
        map cleanly to the new SystemHMO structure.
        """
        prices_without_system_hmo = HMOPrice.objects.filter(system_hmo__isnull=True)
        count = prices_without_system_hmo.count()
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('✅ All HMOPrice records already have system_hmo set.'))
            return
        
        self.stdout.write(self.style.WARNING(
            f'\n⚠️  Found {count} HMOPrice record(s) without system_hmo.\n'
            f'⚠️  This migration will attempt to map them to SystemHMO records.\n'
            f'⚠️  If matching SystemHMOs don\'t exist, this will fail.\n'
        ))
        
        if not force:
            confirm = input('Do you want to continue? [y/N]: ')
            if confirm.lower() != 'y':
                self.stdout.write(self.style.ERROR('❌ Aborted.'))
                return
        
        migrated = 0
        failed = 0
        
        with transaction.atomic():
            for price in prices_without_system_hmo:
                # This migration strategy depends on your specific data structure
                # You may need to customize this logic
                
                # Option 1: Create a placeholder SystemHMO
                system_hmo, created = SystemHMO.objects.get_or_create(
                    name='Migrated - Unknown HMO',
                    defaults={
                        'nhis_number': 'MIGRATION-PLACEHOLDER',
                        'is_active': False,
                    }
                )
                
                if created:
                    self.stdout.write(f'  ℹ️  Created placeholder SystemHMO: {system_hmo.name}')
                
                price.system_hmo = system_hmo
                price.save(update_fields=['system_hmo', 'updated_at'])
                
                migrated += 1
        
        self.stdout.write(self.style.SUCCESS(
            f'\n✅ Migration complete:\n'
            f'  - Migrated: {migrated}\n'
            f'  - Failed: {failed}\n'
        ))
        
        if migrated > 0:
            self.stdout.write(self.style.WARNING(
                f'\n⚠️  NEXT STEPS:\n'
                f'1. Review the migrated HMOPrice records\n'
                f'2. Update them with correct SystemHMO references\n'
                f'3. Delete or deactivate placeholder records\n'
                f'4. Run makemigrations again to make system_hmo non-nullable\n'
            ))