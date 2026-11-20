"""Development-only Spot/UM same-symbol base-quantity hedge demo.

This fixed-target example demonstrates native multi-product hedge coordination;
it does not model alpha, funding income or production execution.
"""
from decimal import Decimal, InvalidOperation

from nautilus_trader.trading import Strategy

DEFAULTS = {"symbol": "ETHUSDT", "quantity": "0.1"}


def prepare(parameters):
    if not isinstance(parameters, dict) or set(parameters) - set(DEFAULTS):
        raise ValueError("现货永续对冲 Demo 参数无效")
    merged = {**DEFAULTS, **parameters}
    symbol = merged["symbol"]
    if not isinstance(symbol, str) or symbol not in {"ETHUSDT", "BTCUSDT"}:
        raise ValueError("当前 Demo 仅支持 ETHUSDT 或 BTCUSDT")
    raw = merged["quantity"]
    if isinstance(raw, bool) or not isinstance(raw, (str, int, Decimal)):
        raise TypeError("对冲数量必须为十进制数")
    try:
        quantity = Decimal(str(raw))
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError("对冲数量格式无效") from exc
    if (
        not quantity.is_finite() or quantity <= 0
        or quantity.as_tuple().exponent < -8
        or len(quantity.as_tuple().digits) > 30
    ):
        raise ValueError("对冲数量必须为不超过8位精度的有限正数")
    return {"symbol": symbol, "quantity": format(quantity, "f")}


class DemoSpotPerpetualHedge(Strategy):
    def on_start(self):
        if (
            not callable(getattr(self, "subscribe_market_bundle", None))
            or not callable(getattr(self, "publish_basket", None))
        ):
            raise TypeError("仅限受限开发环境的原生多产品回测，不支持实盘")
        symbol = self.parameters["symbol"]
        self.subscribe_market_bundle(
            legs=(
                {"product": "spot", "symbol": symbol},
                {"product": "futures_um", "symbol": symbol},
            ),
            data=("closed_bars", "account_positions"),
        )

    def on_market_snapshot(self, snapshot):
        symbol = self.parameters["symbol"]
        if (
            not isinstance(snapshot, dict)
            or not isinstance(snapshot.get("markets"), dict)
            or not isinstance(snapshot.get("positions"), dict)
            or f"spot:{symbol}" not in snapshot["markets"]
            or f"futures_um:{symbol}" not in snapshot["markets"]
        ):
            raise ValueError("同标的现货/永续历史快照不完整")
        quantity = Decimal(self.parameters["quantity"])
        self.publish_basket(
            targets=[
                {"product": "spot", "symbol": symbol, "quantity": format(quantity, "f")},
                {"product": "futures_um", "symbol": symbol, "quantity": format(-quantity, "f")},
            ],
            constraints={"spot_perpetual_hedges": [{"symbol": symbol}]},
        )
