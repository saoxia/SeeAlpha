"""Offline historical audit policy; live orders are checked by the native risk engine.

This predicate validates already-recorded research fills without submitting orders.
It is deliberately not called by current strategy execution code.
"""
from decimal import Decimal

from nautilus_support import make_strategy_price


def opening_order_allowed(instrument, quantity, price) -> bool:
    qty = quantity.as_decimal() if hasattr(quantity, "as_decimal") else Decimal(str(quantity))
    px = price.as_decimal() if hasattr(price, "as_decimal") else Decimal(str(price))
    if not qty.is_finite() or not px.is_finite() or qty <= 0 or px <= 0:
        return False
    if instrument.min_quantity is not None and qty < instrument.min_quantity.as_decimal():
        return False
    if instrument.max_quantity is not None and qty > instrument.max_quantity.as_decimal():
        return False
    notional = instrument.notional_value(instrument.make_qty(qty), make_strategy_price(instrument, px))
    for limit, minimum in ((instrument.min_notional, True), (instrument.max_notional, False)):
        if limit is not None:
            if limit.currency != notional.currency:
                return False
            if minimum and notional.as_decimal() < limit.as_decimal():
                return False
            if not minimum and notional.as_decimal() > limit.as_decimal():
                return False
    return True
