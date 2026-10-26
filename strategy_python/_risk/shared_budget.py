"""单账户中按权重分配、按未释放承诺扣减的周期风险预算。"""

from collections.abc import Callable
from decimal import Decimal


class SharedRiskBudget:
    """在新周期准入时限额；不强平因权益下降而超限的既有周期。"""

    def __init__(self, total_fraction: Decimal, weights: dict[str, Decimal]) -> None:
        if not total_fraction.is_finite() or not 0 < total_fraction <= 1:
            raise ValueError("总风险比例必须位于 (0, 1]")
        if not weights or any(not w.is_finite() or w <= 0 for w in weights.values()):
            raise ValueError("策略风险权重必须为有限正数")
        if sum(weights.values()) != 1:
            raise ValueError("策略风险权重之和必须为 1")
        self.total_fraction = total_fraction
        self.weights = dict(weights)
        self._commitments: dict[str, Callable[[], Decimal]] = {}

    def register(self, key: str, commitment: Callable[[], Decimal]) -> None:
        if key not in self.weights or key in self._commitments:
            raise ValueError("风险预算成员不存在或重复注册")
        self._commitments[key] = commitment

    def available(self, key: str, equity: Decimal) -> Decimal:
        if self._commitments.keys() != self.weights.keys():
            raise RuntimeError("所有策略必须注册后才能使用共享风险预算")
        if not equity.is_finite() or equity <= 0:
            return Decimal(0)
        committed = {name: max(Decimal(0), read()) for name, read in self._commitments.items()}
        limit = equity * self.total_fraction
        own_available = limit * self.weights[key] - committed[key]
        total_available = limit - sum(committed.values())
        return max(Decimal(0), min(own_available, total_available))

    def snapshot(self) -> dict:
        return {
            "total_risk_fraction": str(self.total_fraction),
            "risk_weights": {key: str(value) for key, value in self.weights.items()},
        }
