"""Native multi-symbol ENGINE RESEARCH demonstration, not an alpha strategy.

Fixed ETH Spot and BTC UM target amounts show that a single issued Strategy
can publish two different instruments into one native backtest. The assets do
not automatically hedge; fees, funding and volatility can cause losses.
"""
from decimal import Decimal, InvalidOperation

from nautilus_trader.trading import Strategy

DEFAULTS = {
    "symbol": "BTCUSDT",
    "spot_symbol": "ETHUSDT",
    "spot_quantity": "0.05",
    "perpetual_quantity": "-0.001",
}


def _quantity(value, *, allow_negative=False):
    if isinstance(value, bool):
        raise TypeError("仓位数量格式无效")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("仓位数量必须是有效小数") from exc
    if (
        not number.is_finite() or number == 0
        or (not allow_negative and number < 0)
        or number.as_tuple().exponent < -8
        or len(number.as_tuple().digits) > 30
    ):
        raise ValueError("仓位数量必须是有限非零小数且不超过8位精度")
    return format(number, "f")


def prepare(parameters):
    if not isinstance(parameters, dict) or set(parameters) - set(DEFAULTS):
        raise ValueError("跨标的 Demo 参数无效")
    params = {**DEFAULTS, **parameters}
    if params["symbol"] != "BTCUSDT" or params["spot_symbol"] != "ETHUSDT":
        raise ValueError("此 Demo 仅用于 ETHUSDT 现货与 BTCUSDT 永续")
    spot = _quantity(params["spot_quantity"])
    perp = _quantity(params["perpetual_quantity"], allow_negative=True)
    if Decimal(perp) >= 0:
        raise ValueError("此 Demo 的永续示例必须是空头")
    return {
        "symbol": params["symbol"],
        "spot_symbol": params["spot_symbol"],
        "spot_quantity": spot,
        "perpetual_quantity": perp,
    }


class DemoMultiAssetPortfolio(Strategy):
    def on_start(self):
        if (
            not callable(getattr(self, "subscribe_market_bundle", None))
            or not callable(getattr(self, "publish_basket", None))
        ):
            raise RuntimeError("仅可在原生通用多标的开发回测中使用此 Demo")
        self.subscribe_market_bundle(
            legs=(
                {"product": "spot", "symbol": self.parameters["spot_symbol"]},
                {"product": "futures_um", "symbol": self.parameters["symbol"]},
            ),
            data=("closed_bars", "account_positions"),
        )

    def on_market_snapshot(self, snapshot):
        markets = snapshot.get("markets")
        if (
            not isinstance(markets, dict)
            or "spot:ETHUSDT" not in markets
            or "futures_um:BTCUSDT" not in markets
            or not isinstance(snapshot.get("positions"), dict)
        ):
            raise ValueError("跨标的回测缺少完整市场与账户快照")
        self.publish_basket(targets=[
            {
                "product": "spot", "symbol": self.parameters["spot_symbol"],
                "quantity": self.parameters["spot_quantity"],
            },
            {
                "product": "futures_um", "symbol": self.parameters["symbol"],
                "quantity": self.parameters["perpetual_quantity"],
            },
        ])
