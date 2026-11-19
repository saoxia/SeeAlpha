"""Pure, deterministic EMA trend + Bollinger mean-reversion target calculation.

Each issued instance may publish trend, reversion, or their weighted sum.
Separate instances are netted by the existing Engine PortfolioCoordinator.
No exchange access, order placement, or engine imports belong here.
"""

from collections import deque
from decimal import Decimal, InvalidOperation


DEFAULTS = {
    "symbol": "ETHUSDT",
    "mode": "combined",
    "fast_period": 8,
    "slow_period": 21,
    "bollinger_period": 20,
    "deviation": "2",
    "trend_weight": "0.6",
    "reversion_weight": "0.4",
    "allocation": "1",
    "risk_limit": "1",
}


def _amount(value, name, *, allow_zero=False):
    if isinstance(value, bool):
        raise ValueError(f"{name}必须是有限十进制数")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name}必须是有限十进制数") from None
    if (
        not result.is_finite()
        or result < 0
        or (result == 0 and not allow_zero)
        or not -8 <= result.as_tuple().exponent <= 8
        or len(result.as_tuple().digits) > 30
    ):
        raise ValueError(f"{name}必须是非负的有限十进制数，最多8位小数")
    return result


def _period(value, name, minimum):
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if type(value) is not int or not minimum <= value <= 500:
        raise ValueError(f"{name}必须是 {minimum} 到 500 的整数")
    return value


def prepare_parameters(parameters):
    """Match the issued-package contract: only string and integer values."""
    if not isinstance(parameters, dict) or set(parameters) - set(DEFAULTS):
        raise ValueError("参数无效或包含未知字段")
    values = {**DEFAULTS, **parameters}
    symbol = str(values["symbol"]).upper()
    if symbol != "ETHUSDT":
        raise ValueError("当前 Engine 单品种回测仅支持 ETHUSDT")
    mode = values["mode"]
    if mode not in {"trend", "reversion", "combined"}:
        raise ValueError("mode 必须是 trend、reversion 或 combined")

    fast = _period(values["fast_period"], "fast_period", 2)
    slow = _period(values["slow_period"], "slow_period", 3)
    if fast >= slow:
        raise ValueError("fast_period 必须小于 slow_period")
    bollinger = _period(values["bollinger_period"], "bollinger_period", 5)
    deviation = _amount(values["deviation"], "deviation")
    trend = _amount(values["trend_weight"], "trend_weight", allow_zero=True)
    reversion = _amount(values["reversion_weight"], "reversion_weight", allow_zero=True)
    if trend + reversion > 1 or trend + reversion == 0:
        raise ValueError("信号权重总和必须大于 0 且不超过 1")
    allocation = _amount(values["allocation"], "allocation")
    risk_limit = _amount(
        values["risk_limit"] if "risk_limit" in parameters else allocation, "risk_limit"
    )
    return {
        "symbol": symbol,
        "mode": mode,
        "fast_period": fast,
        "slow_period": slow,
        "bollinger_period": bollinger,
        "deviation": format(deviation, "f"),
        "trend_weight": format(trend, "f"),
        "reversion_weight": format(reversion, "f"),
        "allocation": format(allocation, "f"),
        "risk_limit": format(risk_limit, "f"),
    }


def _clamp(value, bound):
    return max(-bound, min(bound, value))


class MultiSignalModel:
    """On each closed bar, expose each signal's contribution and target.

    EMA is seeded with the first close and warmed through slow_period bars.
    Bollinger uses a population standard deviation over closed prices.
    Reversion intensity is minus the standardized distance from the middle
    band, clipped to [-1, 1]; its sign opposes price deviation.
    """

    def __init__(self, parameters):
        self.parameters = parameters
        self.count = 0
        self.fast = None
        self.slow = None
        self.closes = deque(maxlen=parameters["bollinger_period"])
        self.fast_alpha = Decimal(2) / (parameters["fast_period"] + 1)
        self.slow_alpha = Decimal(2) / (parameters["slow_period"] + 1)

    def on_close(self, price):
        if isinstance(price, bool):
            raise ValueError("收盘价必须为正数")
        try:
            close = Decimal(str(price))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("收盘价必须为正数") from None
        if not close.is_finite() or close <= 0:
            raise ValueError("收盘价必须为正数")

        self.count += 1
        self.fast = (
            close if self.fast is None
            else self.fast + self.fast_alpha * (close - self.fast)
        )
        self.slow = (
            close if self.slow is None
            else self.slow + self.slow_alpha * (close - self.slow)
        )
        self.closes.append(close)

        params = self.parameters
        mode = params["mode"]
        if mode in {"trend", "combined"} and self.count < params["slow_period"]:
            return None
        if mode in {"reversion", "combined"} and len(self.closes) < params["bollinger_period"]:
            return None

        trend_signal = Decimal(1 if self.fast > self.slow else -1 if self.fast < self.slow else 0)
        mean = sum(self.closes, Decimal(0)) / len(self.closes)
        variance = sum(((item - mean) ** 2 for item in self.closes), Decimal(0)) / len(self.closes)
        std = variance.sqrt()
        reversion_signal = (
            _clamp(-(close - mean) / (std * Decimal(params["deviation"])), Decimal(1))
            if std > 0 else Decimal(0)
        )

        allocation = Decimal(params["allocation"])
        trend_target = (
            allocation * Decimal(params["trend_weight"]) * trend_signal
            if mode in {"trend", "combined"} else Decimal(0)
        )
        reversion_target = (
            allocation * Decimal(params["reversion_weight"]) * reversion_signal
            if mode in {"reversion", "combined"} else Decimal(0)
        )
        target = _clamp(
            trend_target + reversion_target, Decimal(params["risk_limit"])
        )
        return {
            "trend_signal": trend_signal,
            "reversion_signal": reversion_signal,
            "trend_contribution": trend_target,
            "reversion_contribution": reversion_target,
            "target": target,
        }
