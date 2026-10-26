"""偏离均值且上冲失败时做空；目标是入场时的五分钟 SMA。"""

from strategy_python.mean_reversion_short_v2_5min.indicators import Features


def mean_reversion_setup(
    f: Features,
    *,
    entry_kind: str,
    regime: str,
    boll_std: float,
    min_mean_distance: float,
    weekend_only: bool = False,
    weekday: int = 0,
) -> bool:
    if weekend_only and weekday < 5:
        return False
    if regime == "flat" and not f.flat:
        return False
    if regime == "down" and not f.down:
        return False
    if f.close / f.ma - 1 < min_mean_distance:
        return False
    upper = f.ma + boll_std * f.std
    if entry_kind == "reentry":
        return f.previous_close > f.previous_upper and f.close < upper
    return f.high >= upper and f.close < f.open
