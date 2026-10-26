"""Frozen development-selected research preset. No live orders are submitted."""

from .strategy import Config, signals, simulate


def create_config():
    return Config(name="time48", hours=48, stop="none", atr_multiple=2.0, reward=0.0)


def run_backtest(minutes, hourly, start, end, *, fee_per_side=0.0005):
    return simulate(
        minutes,
        signals(hourly),
        create_config(),
        start,
        end,
        mode="account",
        cap=0.2,
        risk=0.0,
        fee=fee_per_side,
    )
