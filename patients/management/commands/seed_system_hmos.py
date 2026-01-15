
import csv
import io
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

# Import will work once models_hmo.py is integrated
# from patients.models_hmo import SystemHMO, HMOTier


class Command(BaseCommand):
    help = 'Seed the master list of System HMOs with default tiers'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv',
            type=str,
            help='Path to CSV file with HMO data (columns: name, nhis_number, email)',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing HMOs before seeding (dangerous!)',
        )
        parser.add_argument(
            '--update',
            action='store_true',
            help='Update existing HMOs with new data (preserves IDs)',
        )

    def handle(self, *args, **options):
        # Import here to handle case where models aren't yet created
        try:
            from patients.models import SystemHMO, HMOTier
        except ImportError:
            # Fallback for when models are in patients.models
            try:
                from patients.models import SystemHMO, HMOTier
            except ImportError:
                raise CommandError(
                    "SystemHMO model not found. Please run migrations first."
                )

        if options['clear']:
            self.stdout.write(self.style.WARNING('Clearing all existing System HMOs...'))
            if not options.get('force'):
                confirm = input('This will delete all HMOs and affect patient enrollments. Continue? [y/N]: ')
                if confirm.lower() != 'y':
                    self.stdout.write(self.style.ERROR('Aborted.'))
                    return
            SystemHMO.objects.all().delete()
            self.stdout.write(self.style.SUCCESS('Cleared all System HMOs.'))

        if options['csv']:
            self._import_from_csv(options['csv'], options['update'])
        else:
            self._seed_default_hmos(options['update'])

        # Show summary
        total = SystemHMO.objects.count()
        active = SystemHMO.objects.filter(is_active=True).count()
        self.stdout.write(self.style.SUCCESS(
            f'\nDone! Total HMOs: {total} ({active} active)'
        ))

    def _seed_default_hmos(self, update=False):
        """Seed the default list of Nigerian HMOs."""
        try:
            from patients.models import SystemHMO
        except ImportError:
            from patients.models import SystemHMO

        # Comprehensive list of Nigerian HMOs
        # Source: NHIS and industry knowledge
        default_hmos = [
            # Major National HMOs
            {
                'name': 'Leadway Health',
                'nhis_number': 'NHIS/HMO/001',
                'description': 'One of the largest HMOs in Nigeria',
            },
            {
                'name': 'Hygeia HMO',
                'nhis_number': 'NHIS/HMO/002',
                'description': 'Premier healthcare management organization',
            },
            {
                'name': 'AIICO Multishield',
                'nhis_number': 'NHIS/HMO/003',
                'description': 'AIICO Insurance healthcare arm',
            },
            {
                'name': 'Clearline HMO',
                'nhis_number': 'NHIS/HMO/004',
                'description': 'Comprehensive healthcare coverage provider',
            },
            {
                'name': 'Total Health Trust',
                'nhis_number': 'NHIS/HMO/005',
                'description': 'Trusted healthcare management solutions',
            },
            {
                'name': 'Reliance HMO',
                'nhis_number': 'NHIS/HMO/006',
                'description': 'Innovative digital-first HMO',
            },
            {
                'name': 'Avon HMO',
                'nhis_number': 'NHIS/HMO/007',
                'description': 'Affordable healthcare plans',
            },
            {
                'name': 'Redcare HMO',
                'nhis_number': 'NHIS/HMO/008',
                'description': 'Quality healthcare management',
            },
            {
                'name': 'Princeton Health',
                'nhis_number': 'NHIS/HMO/009',
                'description': 'Corporate healthcare solutions',
            },
            {
                'name': 'Healthcare International (HCI)',
                'nhis_number': 'NHIS/HMO/010',
                'description': 'International standard healthcare',
            },
            {
                'name': 'PharmAccess HMO',
                'nhis_number': 'NHIS/HMO/011',
                'description': 'Healthcare financing solutions',
            },
            {
                'name': 'Police HMO',
                'nhis_number': 'NHIS/HMO/012',
                'description': 'Nigeria Police Force healthcare plan',
            },
            {
                'name': 'Integrated Healthcare Limited (IHL)',
                'nhis_number': 'NHIS/HMO/013',
                'description': 'Integrated healthcare services',
            },
            {
                'name': 'Precious Health',
                'nhis_number': 'NHIS/HMO/014',
                'description': 'Family healthcare solutions',
            },
            {
                'name': 'Sterling Health HMO',
                'nhis_number': 'NHIS/HMO/015',
                'description': 'Sterling Bank healthcare partner',
            },
            {
                'name': 'Metrohealth HMO',
                'nhis_number': 'NHIS/HMO/016',
                'description': 'Metropolitan healthcare coverage',
            },
            {
                'name': 'Venus Medicare',
                'nhis_number': 'NHIS/HMO/017',
                'description': 'Affordable medicare plans',
            },
            {
                'name': 'Mediplan Healthcare',
                'nhis_number': 'NHIS/HMO/018',
                'description': 'Comprehensive health plans',
            },
            {
                'name': 'Grooming Health',
                'nhis_number': 'NHIS/HMO/019',
                'description': 'Growing healthcare solutions',
            },
            {
                'name': 'Zuma Health',
                'nhis_number': 'NHIS/HMO/020',
                'description': 'Modern healthcare management',
            },
            {
                'name': 'Defence Health Maintenance Limited (DHML)',
                'nhis_number': 'NHIS/HMO/021',
                'description': 'Armed Forces healthcare plan',
            },
            {
                'name': 'Novo Health Africa',
                'nhis_number': 'NHIS/HMO/022',
                'description': 'Pan-African healthcare solutions',
            },
            {
                'name': 'Prepaid Medicare Services',
                'nhis_number': 'NHIS/HMO/023',
                'description': 'Prepaid healthcare services',
            },
            {
                'name': 'United Healthcare',
                'nhis_number': 'NHIS/HMO/024',
                'description': 'United healthcare coverage',
            },
            {
                'name': 'Premier Medicaid',
                'nhis_number': 'NHIS/HMO/025',
                'description': 'Premium healthcare services',
            },
            {
                'name': 'Lifecare HMO',
                'nhis_number': 'NHIS/HMO/026',
                'description': 'Lifetime healthcare coverage',
            },
            {
                'name': 'ProHealth HMO',
                'nhis_number': 'NHIS/HMO/027',
                'description': 'Professional healthcare management',
            },
            {
                'name': 'Healthcare Security Limited',
                'nhis_number': 'NHIS/HMO/028',
                'description': 'Secure healthcare solutions',
            },
            {
                'name': 'Anchor HMO',
                'nhis_number': 'NHIS/HMO/029',
                'description': 'Anchored healthcare services',
            },
            {
                'name': 'Total Care Health',
                'nhis_number': 'NHIS/HMO/030',
                'description': 'Total health coverage',
            },
            # Government/State Programs
            {
                'name': 'Lagos State Health Management Agency (LASHMA)',
                'nhis_number': 'NHIS/STATE/LG001',
                'description': 'Lagos State Government health insurance scheme',
            },
            {
                'name': 'Edo State Health Insurance Scheme',
                'nhis_number': 'NHIS/STATE/ED001',
                'description': 'Edo State health insurance program',
            },
            {
                'name': 'Delta State Contributory Health Commission',
                'nhis_number': 'NHIS/STATE/DT001',
                'description': 'Delta State health scheme',
            },
            {
                'name': 'Ogun State Health Insurance Scheme (OGSHIS)',
                'nhis_number': 'NHIS/STATE/OG001',
                'description': 'Ogun State health insurance',
            },
            {
                'name': 'NHIS Formal Sector Programme',
                'nhis_number': 'NHIS/FORMAL/001',
                'description': 'National Health Insurance Scheme - Formal Sector',
            },
            {
                'name': 'NHIS Informal Sector Programme',
                'nhis_number': 'NHIS/INFORMAL/001',
                'description': 'National Health Insurance Scheme - Informal Sector',
            },
        ]

        self.stdout.write('\nSeeding System HMOs...\n')

        created_count = 0
        updated_count = 0
        skipped_count = 0

        for hmo_data in default_hmos:
            name = hmo_data['name']

            existing = SystemHMO.objects.filter(name__iexact=name).first()

            if existing:
                if update:
                    existing.nhis_number = hmo_data.get('nhis_number', existing.nhis_number)
                    existing.description = hmo_data.get('description', existing.description)
                    existing.save(update_fields=['nhis_number', 'description', 'updated_at'])
                    self.stdout.write(f'  📝 Updated: {name}')
                    updated_count += 1
                else:
                    self.stdout.write(f'  ⏭️  Skipped (exists): {name}')
                    skipped_count += 1
            else:
                # Create new HMO (tiers are auto-created by model save())
                hmo = SystemHMO.objects.create(
                    name=name,
                    nhis_number=hmo_data.get('nhis_number', ''),
                    description=hmo_data.get('description', ''),
                    is_active=True,
                )
                self.stdout.write(self.style.SUCCESS(f'  ✅ Created: {name} (+ 3 tiers)'))
                created_count += 1

        self.stdout.write(f'\nCreated: {created_count}, Updated: {updated_count}, Skipped: {skipped_count}')

    def _import_from_csv(self, csv_path, update=False):
        """Import HMOs from a CSV file."""
        try:
            from patients.models import SystemHMO
        except ImportError:
            from patients.models import SystemHMO

        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except FileNotFoundError:
            raise CommandError(f'CSV file not found: {csv_path}')
        except Exception as e:
            raise CommandError(f'Error reading CSV: {e}')

        self.stdout.write(f'\nImporting {len(rows)} HMOs from CSV...\n')

        created_count = 0
        updated_count = 0
        errors = []

        for i, row in enumerate(rows, start=2):  # Start at 2 for Excel row numbers
            name = (row.get('name') or '').strip()

            if not name:
                errors.append(f'Row {i}: Missing name')
                continue

            try:
                existing = SystemHMO.objects.filter(name__iexact=name).first()

                if existing:
                    if update:
                        existing.nhis_number = row.get('nhis_number', existing.nhis_number) or ''
                        existing.email = row.get('email', existing.email) or ''
                        existing.description = row.get('description', existing.description) or ''
                        existing.save()
                        updated_count += 1
                        self.stdout.write(f'  📝 Updated: {name}')
                    else:
                        self.stdout.write(f'  ⏭️  Skipped (exists): {name}')
                else:
                    SystemHMO.objects.create(
                        name=name,
                        nhis_number=row.get('nhis_number', '') or '',
                        email=row.get('email', '') or '',
                        description=row.get('description', '') or '',
                        is_active=True,
                    )
                    created_count += 1
                    self.stdout.write(self.style.SUCCESS(f'  ✅ Created: {name}'))

            except Exception as e:
                errors.append(f'Row {i} ({name}): {str(e)}')

        self.stdout.write(f'\nCreated: {created_count}, Updated: {updated_count}')

        if errors:
            self.stdout.write(self.style.WARNING(f'\nErrors ({len(errors)}):'))
            for error in errors[:10]:  # Show first 10 errors
                self.stdout.write(f'  ⚠️  {error}')
            if len(errors) > 10:
                self.stdout.write(f'  ... and {len(errors) - 10} more errors')