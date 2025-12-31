"""
pharmacy/services/stock_alerts.py

Service for managing low stock notifications.
"""

from django.contrib.auth import get_user_model
from notifications.services.notify import notify_users
from notifications.enums import Topic, Priority
from accounts.enums import UserRole

User = get_user_model()


def get_pharmacy_managers(facility=None, owner=None):
    """
    Get users who should receive low stock alerts.
    Returns SUPER_ADMIN, ADMIN, and PHARMACY users for the given scope.
    """
    target_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.PHARMACY]
    
    if facility:
        # Facility stock: notify facility pharmacy managers
        return User.objects.filter(
            facility=facility,
            role__in=target_roles,
            is_active=True
        )
    elif owner:
        # Independent pharmacy: notify the owner only
        return User.objects.filter(id=owner.id, is_active=True)
    
    return User.objects.none()


def check_and_notify_low_stock(stock_item, force=False):
    """
    Check if stock is low and send notifications to pharmacy managers.
    
    Args:
        stock_item: StockItem instance
        force: If True, send notification even if recently sent (for manual checks)
    
    Returns:
        bool: True if notification was sent, False otherwise
    """
    # Only notify if stock is actually low
    if not stock_item.is_low_stock():
        return False
    
    # Avoid duplicate notifications by using group_key
    # The notification system will update existing notifications with same group_key
    drug = stock_item.drug
    group_key = f"LOW_STOCK:{stock_item.id}"
    
    # Determine scope and get recipients
    if stock_item.facility:
        facility_id = stock_item.facility.id
        recipients = get_pharmacy_managers(facility=stock_item.facility)
        location_info = f"at {stock_item.facility.name}"
    elif stock_item.owner:
        facility_id = None
        recipients = get_pharmacy_managers(owner=stock_item.owner)
        location_info = "in your pharmacy"
    else:
        return False
    
    if not recipients.exists():
        return False
    
    # Calculate percentages for notification body
    threshold = stock_item.get_reorder_threshold()
    current = stock_item.current_qty
    max_stock = stock_item.max_stock_level
    
    if max_stock > 0:
        percentage = int((current / max_stock) * 100)
        percentage_info = f" ({percentage}% of maximum stock)"
    else:
        percentage_info = ""
    
    # Determine priority based on how low the stock is
    if stock_item.is_out_of_stock():
        priority = Priority.URGENT
        title = f"⚠️ OUT OF STOCK: {drug.name}"
        status_msg = "OUT OF STOCK"
    elif current <= (threshold * 0.5):  # Critical low (<=10% of max)
        priority = Priority.HIGH
        title = f"⚠️ CRITICAL LOW STOCK: {drug.name}"
        status_msg = "CRITICAL"
    else:
        priority = Priority.NORMAL
        title = f"⚠️ Low stock alert: {drug.name}"
        status_msg = "LOW"
    
    # Build notification body
    body_parts = [
        f"{drug.name} {drug.strength or ''} {drug.form or ''}".strip(),
        f"Current stock: {current} units{percentage_info}",
        f"Reorder level: {threshold} units",
        f"Status: {status_msg}",
        f"Location: {location_info}",
    ]
    
    body = "\n".join(body_parts)
    
    # Action URL to stock management page
    if stock_item.facility:
        action_url = "/facility/pharmacy/stock"
    else:
        action_url = "/provider/pharmacy/stock"
    
    # Send notifications
    try:
        notify_users(
            users=recipients,
            topic=Topic.VITAL_ALERT,  # Using VITAL_ALERT for stock alerts
            priority=priority,
            title=title,
            body=body,
            data={
                "stock_item_id": stock_item.id,
                "drug_id": drug.id,
                "current_qty": current,
                "reorder_level": threshold,
                "max_stock_level": max_stock,
                "is_out_of_stock": stock_item.is_out_of_stock(),
            },
            facility_id=facility_id,
            action_url=action_url,
            group_key=group_key,
        )
        return True
    except Exception as e:
        # Log error but don't fail the transaction
        print(f"Failed to send low stock notification: {e}")
        return False


def check_all_low_stock(facility=None, owner=None):
    """
    Check all stock items in a facility/pharmacy for low stock and send notifications.
    Useful for periodic batch checks.
    
    Returns:
        dict: Summary of notifications sent
    """
    from pharmacy.models import StockItem
    
    # Get stock items for the scope
    if facility:
        stock_items = StockItem.objects.filter(facility=facility).select_related('drug')
    elif owner:
        stock_items = StockItem.objects.filter(owner=owner).select_related('drug')
    else:
        return {"error": "No facility or owner specified"}
    
    summary = {
        "total_items": 0,
        "low_stock_count": 0,
        "out_of_stock_count": 0,
        "notifications_sent": 0,
    }
    
    for stock_item in stock_items:
        summary["total_items"] += 1
        
        if stock_item.is_out_of_stock():
            summary["out_of_stock_count"] += 1
        elif stock_item.is_low_stock():
            summary["low_stock_count"] += 1
        
        # Send notification if low or out of stock
        if stock_item.is_low_stock() or stock_item.is_out_of_stock():
            if check_and_notify_low_stock(stock_item):
                summary["notifications_sent"] += 1
    
    return summary