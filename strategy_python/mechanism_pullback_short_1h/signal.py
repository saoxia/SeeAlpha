"""弱势日线背景中，反弹曾收在小时均线上方，重新跌回均线时做空。"""

from strategy_python.mechanism_pullback_short_1h.indicators import Features


def short_setup(features: Features) -> bool:
    return (
        features.down_daily
        and features.previous_close >= features.previous_ma
        and features.close < features.ma
    )
