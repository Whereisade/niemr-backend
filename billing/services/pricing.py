# billing/services/pricing.py
"""
Pricing service for resolving service prices based on facility/owner/HMO.

NO DEFAULT PRICES - Facilities must configure their own prices.
Returns None if no price is configured.
"""

from decimal import Decimal
from typing import Optional

from billing.models import Price, Service


def resolve_price(
    service,
    facility=None,
    owner=None,
    hmo=None,
) -> Optional[Decimal]:
    """
    Resolve the price for a service based on priority:
    1. HMO-specific price (if patient has HMO)
    2. Facility-specific price
    3. Owner-specific price (for independent providers)
    4. None (no price configured)
    
    NO DEFAULT PRICES - If no custom price is set, returns None.
    
    Args:
        service: Service instance
        facility: Facility instance (optional)
        owner: User instance for independent providers (optional)
        hmo: HMO instance (optional)
    
    Returns:
        Decimal: The resolved price, or None if no price is configured
    """
    
    # Priority 1: HMO-specific price
    if hmo and facility:
        price_obj = Price.objects.filter(
            service=service,
            facility=facility,
            hmo=hmo,
        ).first()
        if price_obj and price_obj.amount:
            return price_obj.amount
    
    # Priority 2: Facility-specific price
    if facility:
        price_obj = Price.objects.filter(
            service=service,
            facility=facility,
            hmo__isnull=True,
        ).first()
        if price_obj and price_obj.amount:
            return price_obj.amount
    
    # Priority 3: Owner-specific price (independent provider)
    if owner:
        price_obj = Price.objects.filter(
            service=service,
            owner=owner,
            hmo__isnull=True,
        ).first()
        if price_obj and price_obj.amount:
            return price_obj.amount
    
    # Priority 4: No price configured
    return None


def get_service_price_info(
    service,
    facility=None,
    owner=None,
    hmo=None,
):
    """
    Get detailed pricing information for a service.
    
    Returns:
        dict: {
            'facility_price': Decimal or None,
            'is_set': bool (whether price is configured),
        }
    """
    facility_price = None
    is_set = False
    
    # Check facility price
    if facility:
        price_obj = Price.objects.filter(
            service=service,
            facility=facility,
            hmo__isnull=True,
        ).first()
        if price_obj and price_obj.amount:
            facility_price = price_obj.amount
            is_set = True
    
    # Check owner price (independent provider)
    elif owner:
        price_obj = Price.objects.filter(
            service=service,
            owner=owner,
            hmo__isnull=True,
        ).first()
        if price_obj and price_obj.amount:
            facility_price = price_obj.amount
            is_set = True
    
    return {
        'facility_price': facility_price,
        'is_set': is_set,
    }


def get_or_create_price_override(
    service,
    amount,
    facility=None,
    owner=None,
    hmo=None,
    currency="NGN",
):
    """
    Create or update a custom price for a service.
    
    Args:
        service: Service instance
        amount: Price amount (Decimal or float/int)
        facility: Facility instance (optional)
        owner: User instance for independent providers (optional)
        hmo: HMO instance (optional, for HMO-specific pricing)
        currency: Currency code (default: NGN)
    
    Returns:
        Price: The created or updated Price instance
    """
    # Convert amount to Decimal
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    
    # Build filter criteria
    filters = {
        'service': service,
    }
    
    if hmo:
        filters['hmo'] = hmo
    else:
        filters['hmo__isnull'] = True
    
    if facility:
        filters['facility'] = facility
        filters['owner__isnull'] = True
    elif owner:
        filters['owner'] = owner
        filters['facility__isnull'] = True
    else:
        raise ValueError("Either facility or owner must be provided")
    
    # Create or update price
    price_obj, created = Price.objects.update_or_create(
        **filters,
        defaults={
            'amount': amount,
            'currency': currency,
        }
    )
    
    return price_obj


def delete_price_override(
    service,
    facility=None,
    owner=None,
    hmo=None,
):
    """
    Delete a custom price override.
    
    After deletion, resolve_price() will return None for this service.
    
    Args:
        service: Service instance
        facility: Facility instance (optional)
        owner: User instance for independent providers (optional)
        hmo: HMO instance (optional)
    
    Returns:
        bool: True if a price was deleted, False otherwise
    """
    # Build filter criteria
    filters = {
        'service': service,
    }
    
    if hmo:
        filters['hmo'] = hmo
    else:
        filters['hmo__isnull'] = True
    
    if facility:
        filters['facility'] = facility
    elif owner:
        filters['owner'] = owner
    else:
        raise ValueError("Either facility or owner must be provided")
    
    # Delete matching prices
    deleted_count, _ = Price.objects.filter(**filters).delete()
    
    return deleted_count > 0