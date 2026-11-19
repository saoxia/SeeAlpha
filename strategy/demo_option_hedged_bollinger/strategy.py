"""Demo: Bollinger mean reversion with purchased call + put wings.

This is a fail-closed research example, NOT runnable in the current single-
instrument Engine or a claim of guaranteed option protection.
"""
from decimal import Decimal, InvalidOperation  # noqa: I001

from nautilus_trader.trading import Strategy

from .logic import BollingerModel


DEFAULTS = {
    "symbol": "ETHUSDT",
    "put_symbol": "",
    "call_symbol": "",
    "period": 20,
    "deviation": "2",
    "allocation": "0.1",
    "risk_limit": "0.1",
    "option_contracts": "0.1",
    "max_total_premium": "30",
    "min_dte_seconds": 86400,
    "max_age_seconds": 180,
}


def _positive(value, label):
    if isinstance(value, bool):
        raise TypeError(f"{label}必须大于零")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label}必须是十进制数") from None
    if (not number.is_finite() or number <= 0
            or number.as_tuple().exponent < -8 or len(number.as_tuple().digits) > 30):
        raise ValueError(f"{label}必须是有效正数，且小数不超过8位")
    return format(number, "f")


def _integer(value, label, minimum, maximum):
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label}必须在 {minimum} 到 {maximum} 之间")
    return value


def prepare(parameters):
    if not isinstance(parameters, dict) or set(parameters) - set(DEFAULTS):
        raise ValueError("演示策略参数无效或包含未知字段")
    merged = {**DEFAULTS, **parameters}
    symbol = str(merged["symbol"]).upper()
    if symbol not in {"ETHUSDT", "BTCUSDT"}:
        raise ValueError("布林期权演示仅接受 ETHUSDT / BTCUSDT")
    base = symbol.removesuffix("USDT")
    put, call = str(merged["put_symbol"]).upper(), str(merged["call_symbol"]).upper()
    for name, value, ending in (("put_symbol", put, "-P"), ("call_symbol", call, "-C")):
        if value and (not value.startswith(base + "-") or not value.endswith(ending)
                      or len(value) > 50):
            raise ValueError(f"{name}必须为同标的 Binance 期权合约")
    if bool(put) != bool(call):
        raise ValueError("看跌与看涨期权必须同时指定")
    allocation = _positive(merged["allocation"], "allocation")
    return {
        "symbol": symbol,
        "put_symbol": put,
        "call_symbol": call,
        "period": _integer(merged["period"], "period", 5, 500),
        "deviation": _positive(merged["deviation"], "deviation"),
        "allocation": allocation,
        "risk_limit": _positive(
            merged["risk_limit"] if "risk_limit" in parameters else allocation, "risk_limit"
        ),
        "option_contracts": _positive(merged["option_contracts"], "option_contracts"),
        "max_total_premium": _positive(merged["max_total_premium"], "max_total_premium"),
        "min_dte_seconds": _integer(merged["min_dte_seconds"], "min_dte_seconds", 3600, 30*86400),
        "max_age_seconds": _integer(merged["max_age_seconds"], "max_age_seconds", 1, 3600),
    }


class DemoOptionHedgedBollinger(Strategy):
    def on_start(self):
        if not callable(getattr(self, "publish_basket", None)) or not callable(
            getattr(self, "subscribe_market_bundle", None)
        ):
            raise TypeError(
                "当前 Engine 不支持跨永续与期权的原子多腿目标、"
                "先成交保护腿的门禁及完整回测；禁止裸方向仓位启动。"
            )
        if not self.parameters["put_symbol"] or not self.parameters["call_symbol"]:
            raise RuntimeError("必须先选择同标的、同到期日的看跌和看涨期权")
        self.model = BollingerModel(self.parameters["period"], self.parameters["deviation"])
        self.subscribe_market_bundle(
            symbol=self.parameters["symbol"],
            products=("futures_um", "options"),
            option_symbols=(self.parameters["put_symbol"], self.parameters["call_symbol"]),
            data=("closed_bars", "option_quotes", "option_instruments", "positions"),
        )

    def on_multi_market_snapshot(self, snapshot):
        plan = self.model.propose(snapshot, self.parameters)
        if plan["action"] not in {"enter_long", "enter_short", "exit"}:
            return
        # The executor MUST buy/verify sufficient protection BEFORE increasing
        # perpetual exposure; no fallback to a naked perpetual order is allowed.
        self.publish_basket(
            targets=plan["targets"],
            constraints={"hedges_filled_before_risk": True, "atomic_reconciliation": True},
        )
        self.model.commit(plan)
