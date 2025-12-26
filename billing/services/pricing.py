from decimal import Decimal

from billing.models import Price, Service
from typing import Optional

from facilities.models import Facility


def resolve_price(*, service: Service, facility: Optional[Facility] = None, owner=None) -> Decimal:
    """Resolve price for a service.

    Priority:
    - facility override (facility pricing)
    - owner override (independent provider pricing)
    - service.default_price
    """
    if facility is not None:
        p = Price.objects.filter(facility=facility, owner__isnull=True, service=service).first()
        if p:
            return p.amount

    if owner is not None:
        p = Price.objects.filter(owner=owner, facility__isnull=True, service=service).first()
        if p:
            return p.amount

    return service.default_price
