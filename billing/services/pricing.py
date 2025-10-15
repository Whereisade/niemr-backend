from decimal import Decimal
from facilities.models import Facility
from patients.models import Patient
from billing.models import Service, Price

def resolve_price(*, facility: Facility, service: Service) -> Decimal:
    p = Price.objects.filter(facility=facility, service=service).first()
    return p.amount if p else service.default_price
