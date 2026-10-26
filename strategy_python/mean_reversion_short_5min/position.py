"""ETHUSDT v4 的分层目标表达。"""

from dataclasses import dataclass
from decimal import Decimal
from math import isfinite


def first_take_profit_price(
    entry_price: Decimal | None, bollinger: tuple[float, float, float] | None,
    minimum_width: float | None,
) -> Decimal | None:
    """按最新下轨与最近一次建仓成交价减去240根最小带宽取低价。"""
    if (bollinger is None or minimum_width is None or not isfinite(minimum_width)
            or minimum_width < 0 or not isfinite(bollinger[0])
            or entry_price is None or not entry_price.is_finite() or entry_price <= 0):
        return None
    price = min(Decimal(str(bollinger[0])), entry_price - Decimal(str(minimum_width)))
    return price if price > 0 else None


def take_profit_prices(
    bollinger: tuple[float, float, float] | None, ma99: float | None,
    distance_mean: float | None,
) -> tuple[Decimal, Decimal] | None:
    """返回原下轨基准及第二止盈价；第一止盈由独立宽度公式覆盖。"""
    if bollinger is None or ma99 is None or distance_mean is None:
        return None
    lower, threshold = bollinger[0], ma99 + distance_mean
    if not isfinite(lower) or not isfinite(threshold) or min(lower, threshold) <= 0:
        return None
    return Decimal(str(lower)), Decimal(str(min(lower, threshold)))


@dataclass(frozen=True)
class EntryTarget:
    """风险层批准后希望挂出的单层限价目标。"""

    direction: int
    role: str
    quantity: Decimal
    price: Decimal
    risk_per_quantity: Decimal = Decimal(0)
    planned_risk: Decimal = Decimal(0)
    risk_context: dict[str, float] | None = None


def fixed_stop_price(average: Decimal) -> Decimal:
    """首仓和两份持仓按当前持仓均价上方60%止损。"""
    if not average.is_finite() or average <= 0:
        return Decimal(0)
    return average * Decimal("1.6")



def remainder_stop_price(
    average: Decimal, bollinger: tuple[float, float, float] | None,
    boll_multiple: float,
) -> Decimal | None:
    """余仓按当前均价及最新 BOLL 中轨 + 0.5σ 止损。"""
    if bollinger is None or not all(isfinite(value) for value in bollinger):
        return None
    _, middle, upper = bollinger
    if not isfinite(boll_multiple) or boll_multiple <= 0 or upper < middle:
        return None
    sigma = (Decimal(str(upper)) - Decimal(str(middle))) / Decimal(str(boll_multiple))
    return min(average, Decimal(str(middle)) + Decimal("0.5") * sigma)


def add_entry_price(average: Decimal, bollinger: tuple[float, float, float] | None,
                    ma99: float | None, distance: Decimal | None) -> Decimal | None:
    if bollinger is None or not isfinite(bollinger[2]) or average <= 0:
        return None
    minimum = average * Decimal("1.005")
    upper = Decimal(str(bollinger[2]))
    if upper >= minimum:
        return upper
    if ma99 is None or not isfinite(ma99) or distance is None or not distance.is_finite():
        return None
    return max(minimum, Decimal(str(ma99)) + distance)
