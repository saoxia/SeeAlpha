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
    # [风险预算恒等式] 每单位损失=止损价格差+入场/止损两边金额的成本缓冲。
    # 数量由预算/单位损失推导，再限名义敞口；fee_buffer不是实际手续费扣款。
    unit_risk = stop - entry + (entry + stop) * Decimal(str(fee_buffer))
    qty = min(
        equity * Decimal(str(risk_fraction)) / unit_risk,
        equity * Decimal(str(max_notional_fraction)) / entry,
    )
    return (qty / increment).to_integral_value(rounding=ROUND_DOWN) * increment
