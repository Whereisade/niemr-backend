# billing/services/pricing.py
"""
Pricing service for resolving service prices based on facility/owner/HMO.

UPDATED: Now supports System-scoped HMOs with tier-specific pricing.

Pricing Resolution Priority:
1. HMO + Tier specific price (most specific)
2. HMO level price (no tier specified)
3. Facility/Owner base price
4. None (no price configured)

NO DEFAULT PRICES - Facilities must configure their own prices.
Returns None if no price is configured.
"""

from decimal import Decimal
from typing import Optional, Dict, Any

from billing.models import Price, Service, HMOPrice


def resolve_price(
    service,
    facility=None,
    owner=None,
    system_hmo=None,
    tier=None,
) -> Optional[Decimal]:
    """
    Resolve the price for a service based on priority:
    1. HMO + Tier specific price (if patient has HMO with tier) - uses HMOPrice model
    2. HMO level price (HMO without tier specified)
    3. Facility-specific price - uses Price model
    4. Owner-specific price (for independent providers) - uses Price model
    5. None (no price configured)
    
    NO DEFAULT PRICES - If no custom price is set, returns None.
    
    Args:
        service: Service instance
        facility: Facility instance (optional)
        owner: User instance for independent providers (optional)
        system_hmo: SystemHMO instance (optional) - NEW
        tier: HMOTier instance (optional) - NEW
    
    Returns:
        Decimal: The resolved price, or None if no price is configured
    """
    
    # Priority 1 & 2: HMO-specific price (uses HMOPrice model)
    if system_hmo:
        hmo_price = _resolve_hmo_price(service, facility, owner, system_hmo, tier)
        if hmo_price is not None:
            return hmo_price
    
    # Priority 3: Facility-specific price (uses Price model)
    if facility:
        price_obj = Price.objects.filter(
            service=service,
            facility=facility,
            owner__isnull=True  # Facility pricing only
        ).first()
        if price_obj and price_obj.amount:
            return price_obj.amount
    
    # Priority 4: Owner-specific price (independent provider)
    if owner:
        price_obj = Price.objects.filter(
            service=service,
            owner=owner,
            facility__isnull=True  # Owner pricing only
        ).first()
        if price_obj and price_obj.amount:
            return price_obj.amount
    
    # Priority 5: No price configured
    return None


def _resolve_hmo_price(
    service,
    facility,
    owner,
    system_hmo,
    tier,
) -> Optional[Decimal]:
    """
    Resolve HMO-specific pricing with tier support.
    
    Priority:
    1. HMO + Tier specific price (exact match)
    2. HMO level price (tier is null in HMOPrice)
    
    Args:
        service: Service instance
        facility: Facility instance (or None for owner)
        owner: User instance (or None for facility)
        system_hmo: SystemHMO instance
        tier: HMOTier instance (optional)
    
    Returns:
        Decimal or None
    """
    try:
        # Build base query
        base_filter = {
            'service': service,
            'system_hmo': system_hmo,
            'is_active': True,
        }
        
        if facility:
            base_filter['facility'] = facility
            base_filter['owner__isnull'] = True
        elif owner:
            base_filter['owner'] = owner
            base_filter['facility__isnull'] = True
        else:
            return None
        
        # Priority 1: Tier-specific price
        if tier:
            hmo_price = HMOPrice.objects.filter(
                **base_filter,
                tier=tier,
            ).first()
            if hmo_price and hmo_price.amount:
                return hmo_price.amount
        
        # Priority 2: HMO-level price (no tier)
        hmo_price = HMOPrice.objects.filter(
            **base_filter,
            tier__isnull=True,
        ).first()
        if hmo_price and hmo_price.amount:
            return hmo_price.amount
        
        return None
        
    except Exception:
        # HMOPrice table might not exist or other error - continue to regular pricing
        return None


def get_service_price_info(
    service,
    facility=None,
    owner=None,
    system_hmo=None,
    tier=None,
) -> Dict[str, Any]:
    """
    Get detailed pricing information for a service.
    
    Returns:
        dict: {
            'resolved_price': Decimal or None,
            'price_source': 'hmo_tier' | 'hmo' | 'facility' | 'owner' | None,
            'hmo_tier_price': Decimal or None,
            'hmo_price': Decimal or None,
            'facility_price': Decimal or None,
            'is_set': bool (whether any price is configured),
        }
    """
    result = {
        'resolved_price': None,
        'price_source': None,
        'hmo_tier_price': None,
        'hmo_price': None,
        'facility_price': None,
        'is_set': False,
    }
    
    # Check HMO prices
    if system_hmo:
        base_filter = {
            'service': service,
            'system_hmo': system_hmo,
            'is_active': True,
        }
        
        if facility:
            base_filter['facility'] = facility
            base_filter['owner__isnull'] = True
        elif owner:
            base_filter['owner'] = owner
            base_filter['facility__isnull'] = True
        
        try:
            # Check tier-specific price
            if tier:
                hmo_tier_price = HMOPrice.objects.filter(
                    **base_filter,
                    tier=tier,
                ).first()
                if hmo_tier_price and hmo_tier_price.amount:
                    result['hmo_tier_price'] = hmo_tier_price.amount
                    if result['resolved_price'] is None:
                        result['resolved_price'] = hmo_tier_price.amount
                        result['price_source'] = 'hmo_tier'
                        result['is_set'] = True
            
            # Check HMO-level price
            hmo_price = HMOPrice.objects.filter(
                **base_filter,
                tier__isnull=True,
            ).first()
            if hmo_price and hmo_price.amount:
                result['hmo_price'] = hmo_price.amount
                if result['resolved_price'] is None:
                    result['resolved_price'] = hmo_price.amount
                    result['price_source'] = 'hmo'
                    result['is_set'] = True
        except Exception:
            pass
    
    # Check facility price
    if facility:
        price_obj = Price.objects.filter(
            service=service,
            facility=facility,
            owner__isnull=True
        ).first()
        if price_obj and price_obj.amount:
            result['facility_price'] = price_obj.amount
            if result['resolved_price'] is None:
                result['resolved_price'] = price_obj.amount
                result['price_source'] = 'facility'
                result['is_set'] = True
    
    # Check owner price
    elif owner:
        price_obj = Price.objects.filter(
            service=service,
            owner=owner,
            facility__isnull=True
        ).first()
        if price_obj and price_obj.amount:
            result['facility_price'] = price_obj.amount  # Same key for consistency
            if result['resolved_price'] is None:
                result['resolved_price'] = price_obj.amount
                result['price_source'] = 'owner'
                result['is_set'] = True
    
    return result


def get_or_create_price_override(
    service,
    amount,
    facility=None,
    owner=None,
    system_hmo=None,
    tier=None,
    currency="NGN",
):
    """
    Create or update a custom price for a service.
    
    If system_hmo is provided, creates HMOPrice entry.
    Otherwise, creates regular Price entry.
    
    Args:
        service: Service instance
        amount: Price amount (Decimal or float/int)
        facility: Facility instance (optional)
        owner: User instance for independent providers (optional)
        system_hmo: SystemHMO instance (optional, for HMO-specific pricing)
        tier: HMOTier instance (optional, for tier-specific HMO pricing)
        currency: Currency code (default: NGN)
    
    Returns:
        Price or HMOPrice: The created or updated price instance
    """
    # Convert amount to Decimal
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    
    # HMO-specific pricing (uses HMOPrice model)
    if system_hmo:
        if not facility and not owner:
            raise ValueError("Either facility or owner must be provided for HMO pricing")
        
        filters = {
            'system_hmo': system_hmo,
            'service': service,
            'tier': tier,  # Can be None for HMO-level pricing
        }
        
        if facility:
            filters['facility'] = facility
            filters['owner'] = None
        else:
            filters['owner'] = owner
            filters['facility'] = None
        
        price_obj, created = HMOPrice.objects.update_or_create(
            **filters,
            defaults={
                'amount': amount,
                'currency': currency,
                'is_active': True,
            }
        )
        return price_obj
    
    # Regular pricing (uses Price model)
    filters = {'service': service}
    
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
    system_hmo=None,
    tier=None,
):
    """
    Delete a custom price override.
    
    After deletion, resolve_price() will fall back to next priority level.
    
    Args:
        service: Service instance
        facility: Facility instance (optional)
        owner: User instance for independent providers (optional)
        system_hmo: SystemHMO instance (optional)
        tier: HMOTier instance (optional)
    
    Returns:
        bool: True if a price was deleted, False otherwise
    """
    # HMO-specific pricing
    if system_hmo:
        filters = {
            'system_hmo': system_hmo,
            'service': service,
        }
        
        if tier:
            filters['tier'] = tier
        else:
            filters['tier__isnull'] = True
        
        if facility:
            filters['facility'] = facility
            filters['owner__isnull'] = True
        elif owner:
            filters['owner'] = owner
            filters['facility__isnull'] = True
        else:
            raise ValueError("Either facility or owner must be provided for HMO pricing")
        
        deleted_count, _ = HMOPrice.objects.filter(**filters).delete()
        return deleted_count > 0
    
    # Regular pricing
    filters = {'service': service}
    
    if facility:
        filters['facility'] = facility
        filters['owner__isnull'] = True
    elif owner:
        filters['owner'] = owner
        filters['facility__isnull'] = True
    else:
        raise ValueError("Either facility or owner must be provided")
    
    # Delete matching prices
    deleted_count, _ = Price.objects.filter(**filters).delete()
    
    return deleted_count > 0


def get_all_hmo_prices_for_service(
    service,
    facility=None,
    owner=None,
) -> list:
    """
    Get all HMO prices configured for a service at a facility/provider.
    
    Useful for pricing management interfaces.
    
    Returns:
        list: List of dicts with HMO pricing info
    """
    if not facility and not owner:
        return []
    
    filters = {'service': service, 'is_active': True}
    
    if facility:
        filters['facility'] = facility
        filters['owner__isnull'] = True
    else:
        filters['owner'] = owner
        filters['facility__isnull'] = True
    
    prices = HMOPrice.objects.filter(**filters).select_related(
        'system_hmo', 'tier'
    ).order_by('system_hmo__name', 'tier__level')
    
    result = []
    for price in prices:
        result.append({
            'id': price.id,
            'system_hmo_id': price.system_hmo_id,
            'system_hmo_name': price.system_hmo.name,
            'tier_id': price.tier_id,
            'tier_name': price.tier.name if price.tier else None,
            'tier_level': price.tier.level if price.tier else None,
            'amount': price.amount,
            'currency': price.currency,
            'is_active': price.is_active,
        })
    
    return result


# =============================================================================
# LEGACY SUPPORT
# =============================================================================

def resolve_price_legacy(
    service,
    facility=None,
    owner=None,
    hmo=None,
) -> Optional[Decimal]:
    """
    LEGACY: Resolve price using old facility-scoped HMO model.
    
    Maintained for backward compatibility during migration.
    Use resolve_price() with system_hmo for new code.
    
    Args:
        service: Service instance
        facility: Facility instance (optional)
        owner: User instance for independent providers (optional)
        hmo: HMO instance (old model, optional)
    
    Returns:
        Decimal: The resolved price, or None if no price is configured
    """
    
    # Priority 1: HMO-specific price using OLD HMO model
    if hmo and facility:
        try:
            # Try to find price with old 'hmo' FK
            hmo_price = HMOPrice.objects.filter(
                service=service,
                facility=facility,
                is_active=True
            ).first()
            
            # Check if there's an 'hmo' field (legacy)
            if hmo_price and hasattr(hmo_price, 'hmo') and hmo_price.hmo_id == hmo.id:
                if hmo_price.amount:
                    return hmo_price.amount
        except Exception:
            pass
    
    # Fall back to regular pricing
    if facility:
        price_obj = Price.objects.filter(
            service=service,
            facility=facility,
            owner__isnull=True
        ).first()
        if price_obj and price_obj.amount:
            return price_obj.amount
    
    if owner:
        price_obj = Price.objects.filter(
            service=service,
            owner=owner,
            facility__isnull=True
        ).first()
        if price_obj and price_obj.amount:
            return price_obj.amount
    
    return None