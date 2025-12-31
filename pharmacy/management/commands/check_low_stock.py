"""
pharmacy/management/commands/check_low_stock.py

Management command to check for low stock items and send notifications.

This command should be run periodically (e.g., daily) via cron or task scheduler to ensure
pharmacy managers are alerted about low stock levels.

Usage:
    python manage.py check_low_stock                      # Check all facilities and independent pharmacies
    python manage.py check_low_stock --facility 1         # Check specific facility only
    python manage.py check_low_stock --dry-run            # Show what would be done without sending notifications
    python manage.py check_low_stock --force              # Force send notifications even if recently sent
"""

from django.core.management.base import BaseCommand
from django.db.models import Q
from pharmacy.models import StockItem
from pharmacy.services.stock_alerts import check_and_notify_low_stock, check_all_low_stock
from facilities.models import Facility
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = "Check for low stock items and send notifications to pharmacy managers"

    def add_arguments(self, parser):
        parser.add_argument(
            "--facility",
            type=int,
            help="Check specific facility ID only (otherwise checks all)",
        )
        parser.add_argument(
            "--owner",
            type=int,
            help="Check specific independent pharmacy owner ID only",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be checked without sending notifications",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force send notifications even if recently sent",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]
        facility_id = options.get("facility")
        owner_id = options.get("owner")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No notifications will be sent"))

        # Build queryset based on scope
        if facility_id:
            # Check specific facility
            try:
                facility = Facility.objects.get(id=facility_id)
                self.stdout.write(f"Checking facility: {facility.name}")
                
                if not dry_run:
                    summary = check_all_low_stock(facility=facility)
                    self._display_summary(summary, f"Facility {facility.name}")
                else:
                    stock_items = StockItem.objects.filter(facility=facility).select_related('drug')
                    self._dry_run_check(stock_items)
                    
            except Facility.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Facility with ID {facility_id} not found"))
                return

        elif owner_id:
            # Check specific independent pharmacy
            try:
                owner = User.objects.get(id=owner_id)
                self.stdout.write(f"Checking independent pharmacy: {owner.email}")
                
                if not dry_run:
                    summary = check_all_low_stock(owner=owner)
                    self._display_summary(summary, f"Owner {owner.email}")
                else:
                    stock_items = StockItem.objects.filter(owner=owner).select_related('drug')
                    self._dry_run_check(stock_items)
                    
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"User with ID {owner_id} not found"))
                return

        else:
            # Check all facilities and independent pharmacies
            self.stdout.write("Checking all facilities and independent pharmacies...")
            
            total_notifications = 0
            total_low_stock = 0
            total_out_of_stock = 0
            total_items = 0
            
            # Check all facilities
            facilities = Facility.objects.all()
            for facility in facilities:
                self.stdout.write(f"\nChecking facility: {facility.name}")
                
                if not dry_run:
                    summary = check_all_low_stock(facility=facility)
                    total_notifications += summary.get("notifications_sent", 0)
                    total_low_stock += summary.get("low_stock_count", 0)
                    total_out_of_stock += summary.get("out_of_stock_count", 0)
                    total_items += summary.get("total_items", 0)
                    self._display_summary(summary, facility.name)
                else:
                    stock_items = StockItem.objects.filter(facility=facility).select_related('drug')
                    self._dry_run_check(stock_items)
            
            # Check all independent pharmacies
            from accounts.enums import UserRole
            independent_pharmacies = User.objects.filter(
                role=UserRole.PHARMACY,
                facility__isnull=True,
                is_active=True
            )
            
            for owner in independent_pharmacies:
                self.stdout.write(f"\nChecking independent pharmacy: {owner.email}")
                
                if not dry_run:
                    summary = check_all_low_stock(owner=owner)
                    total_notifications += summary.get("notifications_sent", 0)
                    total_low_stock += summary.get("low_stock_count", 0)
                    total_out_of_stock += summary.get("out_of_stock_count", 0)
                    total_items += summary.get("total_items", 0)
                    self._display_summary(summary, owner.email)
                else:
                    stock_items = StockItem.objects.filter(owner=owner).select_related('drug')
                    self._dry_run_check(stock_items)
            
            if not dry_run:
                # Overall summary
                self.stdout.write("\n" + "=" * 60)
                self.stdout.write(self.style.SUCCESS("OVERALL SUMMARY"))
                self.stdout.write(f"Total items checked: {total_items}")
                self.stdout.write(self.style.WARNING(f"Low stock items: {total_low_stock}"))
                self.stdout.write(self.style.ERROR(f"Out of stock items: {total_out_of_stock}"))
                self.stdout.write(self.style.SUCCESS(f"Notifications sent: {total_notifications}"))
                self.stdout.write("=" * 60)

    def _dry_run_check(self, stock_items):
        """Display what would be checked in dry run mode."""
        total = stock_items.count()
        low_stock_count = 0
        out_of_stock_count = 0
        
        for stock_item in stock_items:
            if stock_item.is_out_of_stock():
                out_of_stock_count += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"  OUT OF STOCK: {stock_item.drug.name} "
                        f"(Current: {stock_item.current_qty}, Threshold: {stock_item.get_reorder_threshold()})"
                    )
                )
            elif stock_item.is_low_stock():
                low_stock_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  LOW STOCK: {stock_item.drug.name} "
                        f"(Current: {stock_item.current_qty}, Threshold: {stock_item.get_reorder_threshold()})"
                    )
                )
        
        self.stdout.write(f"\nWould notify for {low_stock_count + out_of_stock_count} items:")
        self.stdout.write(f"  - Low stock: {low_stock_count}")
        self.stdout.write(f"  - Out of stock: {out_of_stock_count}")
        self.stdout.write(f"  - Total items checked: {total}")

    def _display_summary(self, summary, location_name):
        """Display summary of notifications sent."""
        if "error" in summary:
            self.stdout.write(self.style.ERROR(f"  Error: {summary['error']}"))
            return
        
        self.stdout.write(f"  Total items: {summary.get('total_items', 0)}")
        
        low_stock = summary.get('low_stock_count', 0)
        if low_stock > 0:
            self.stdout.write(self.style.WARNING(f"  Low stock items: {low_stock}"))
        
        out_of_stock = summary.get('out_of_stock_count', 0)
        if out_of_stock > 0:
            self.stdout.write(self.style.ERROR(f"  Out of stock items: {out_of_stock}"))
        
        notifications = summary.get('notifications_sent', 0)
        if notifications > 0:
            self.stdout.write(self.style.SUCCESS(f"  Notifications sent: {notifications}"))
        else:
            self.stdout.write("  No notifications needed")