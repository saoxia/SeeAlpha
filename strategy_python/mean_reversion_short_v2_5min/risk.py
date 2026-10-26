"""结构止损、含成本定仓；限制单笔风险和名义敞口。"""

from decimal import ROUND_DOWN, Decimal


def quantity_for_risk(
    equity: Decimal,
    entry: Decimal,
    stop: Decimal,
    *,
    risk_fraction: float,
    max_notional_fraction: float,
    fee_buffer: float,
    increment: Decimal,
) -> Decimal:
    if equity <= 0 or entry <= 0 or stop <= entry:
        return Decimal(0)
    unit_risk = stop - entry + (entry + stop) * Decimal(str(fee_buffer))
    qty = min(
        equity * Decimal(str(risk_fraction)) / unit_risk,
        # Reserve entry costs as well as margin when the notional cap binds.
        equity
        * Decimal(str(max_notional_fraction))
        / (entry * (1 + Decimal(str(max_notional_fraction)) * Decimal(str(fee_buffer)))),
    )
    return (qty / increment).to_integral_value(rounding=ROUND_DOWN) * increment
