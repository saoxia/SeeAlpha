"""Demo: buy spot and short the same base quantity of perpetual futures.

Requires a future basket-capable Engine adapter; the present single-symbol
PortfolioCoordinator cannot safely execute an atomic cross-product hedge.
"""
from decimal import Decimal, InvalidOperation

from nautilus_trader.trading import Strategy

from .logic import snapshot_plan


DEFAULTS = {
    "symbol": "ETHUSDT",
    "allocation": "0.1",
    "risk_limit": "0.1",
    "min_funding_rate": "0.00005",
    "max_basis_bps": "30",
    "max_leg_drift": "0.001",
    "max_age_seconds": 180,
}


def _number(value, key, *, zero=False):
    if isinstance(value, bool):
        raise ValueError(f"{key}必须是有限十进制数")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{key}必须是有限十进制数") from None
    if not amount.is_finite() or amount < 0 or (not zero and amount == 0):
        raise ValueError(f"{key}必须是非负有限十进制数")
    if amount.as_tuple().exponent < -8 or len(amount.as_tuple().digits) > 30:
        raise ValueError(f"{key}精度超限")
    return format(amount, "f")


def prepare(parameters):
    if not isinstance(parameters, dict) or set(parameters) - set(DEFAULTS):
        raise ValueError("演示策略参数无效或包含未知字段")
    merged = {**DEFAULTS, **parameters}
    symbol = str(merged["symbol"]).upper()
    if symbol not in {"ETHUSDT", "BTCUSDT"}:
        raise ValueError("资金费率演示仅接受 ETHUSDT / BTCUSDT")
    age = merged["max_age_seconds"]
    if isinstance(age, str) and age.isdigit():
        age = int(age)
    if type(age) is not int or not 1 <= age <= 3600:
        raise ValueError("max_age_seconds 必须在 1 到 3600 秒之间")
    allocation = _number(merged["allocation"], "allocation")
    risk_limit = _number(
        merged["risk_limit"] if "risk_limit" in parameters else allocation, "risk_limit"
    )
    return {
        "symbol": symbol,
        "allocation": allocation,
        "risk_limit": risk_limit,
        "min_funding_rate": _number(merged["min_funding_rate"], "min_funding_rate", zero=True),
        "max_basis_bps": _number(merged["max_basis_bps"], "max_basis_bps", zero=True),
        "max_leg_drift": _number(merged["max_leg_drift"], "max_leg_drift", zero=True),
        "max_age_seconds": age,
    }


class DemoFundingCarry(Strategy):
    """Research intent only until an Engine implements atomic multi-leg baskets."""

    def on_start(self):
        if not callable(getattr(self, "publish_basket", None)) or not callable(
            getattr(self, "subscribe_market_bundle", None)
        ):
            raise RuntimeError(
                "当前 Engine 仅支持单品种目标；本 Demo 需要跨现货/永续行情、"
                "原子多腿目标、成交补偿和对账能力，禁止降级成单腿交易。"
            )
        self.subscribe_market_bundle(
            symbol=self.parameters["symbol"],
            products=("spot", "futures_um"),
            data=("closed_bars", "funding_rate", "account_positions"),
        )

    def on_market_snapshot(self, snapshot):
        plan = snapshot_plan(snapshot, self.parameters)
        if plan["action"] == "blocked":
            self.log.error(plan["reason"])
            return
        # The basket executor must account for cash vs margin, fees, limits,
        # fills, margin and partial-fill recovery BEFORE exposing new risk.
        self.publish_basket(
            targets=plan["targets"],
            constraints={"atomic_hedge": True, "max_leg_drift": self.parameters["max_leg_drift"]},
        )
