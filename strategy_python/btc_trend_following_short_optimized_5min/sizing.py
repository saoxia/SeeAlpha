"""按盯市权益、当前价格与目标合约步长计算数量。"""

from decimal import ROUND_DOWN, Decimal


def equity_quantity(
    equity: Decimal,
    price: Decimal,
    fraction: Decimal,
    size_increment: Decimal,
) -> Decimal:
    if equity <= 0 or price <= 0 or fraction <= 0 or size_increment <= 0:
        return Decimal(0)
    # [会计恒等式] 数量=目标名义金额/价格；名义金额=盯市权益×预算比例。
    # price和size_increment来自目标合约，fraction是预算，不是统计估计。
    raw = equity * fraction / price
    return (raw / size_increment).to_integral_value(rounding=ROUND_DOWN) * size_increment
