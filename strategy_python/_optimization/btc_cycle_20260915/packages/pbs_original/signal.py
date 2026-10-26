"""弱势日线背景中，反弹曾收在小时均线上方，重新跌回均线时做空。"""

from strategy_python._optimization.btc_cycle_20260915.packages.pbs_original.indicators import (
    Features,
)


def short_setup(features: Features) -> bool:
    return (
        features.down_daily
        and features.previous_close >= features.previous_ma
        and features.close < features.ma
    )
