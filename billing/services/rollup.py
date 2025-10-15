from decimal import Decimal
from billing.models import Charge

def recompute_charge_status(charge: Charge):
    paid = sum(a.amount for a in charge.allocations.all())
    if paid <= 0:
        charge.status = "UNPAID"
    elif paid < charge.amount:
        charge.status = "PARTIALLY_PAID"
    else:
        charge.status = "PAID"
    charge.save(update_fields=["status"])
