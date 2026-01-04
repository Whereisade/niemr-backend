from decimal import Decimal
from typing import Optional

from billing.models import Price, Service, HMOPrice
from facilities.models import Facility


def resolve_price(*, service: Service, facility: Optional[Facility] = None, owner=None, patient=None) -> Decimal:
    """Resolve price for a service.

    Priority:
    - HMO override (facility + patient's HMO) when patient is insured and HMO belongs to facility
    - facility override (facility pricing)
    - owner override (independent provider pricing)
    - service.default_price
    """
    # 1) HMO override (facility + patient)
    if facility is not None and patient is not None:
        try:
            from patients.enums import InsuranceStatus  # local import to avoid circular deps
            hmo = getattr(patient, "hmo", None)
            if hmo and getattr(patient, "insurance_status", None) == InsuranceStatus.INSURED:
                if getattr(hmo, "facility_id", None) == getattr(facility, "id", None):
                    hp = (
                        HMOPrice.objects.filter(
                            facility=facility,
                            hmo=hmo,
                            service=service,
                            is_active=True,
                        )
                        .order_by("-id")
                        .first()
                    )
                    if hp:
                        return hp.amount
        except Exception:
            # If anything about insurance/HMO resolution fails, fall back gracefully.
            pass

    # 2) Facility override
    if facility is not None:
        p = (
            Price.objects.filter(
                facility=facility,
                owner__isnull=True,
                service=service,
                is_active=True,
            )
            .order_by("-id")
            .first()
        )
        if p:
            return p.amount

    # 3) Owner override (independent provider pricing)
    if owner is not None:
        p = (
            Price.objects.filter(
                owner=owner,
                facility__isnull=True,
                service=service,
                is_active=True,
            )
            .order_by("-id")
            .first()
        )
        if p:
            return p.amount

    return service.default_price
