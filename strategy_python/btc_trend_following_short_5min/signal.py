"""MA20 趋势策略的分批退出信号。"""

from decimal import Decimal
from enum import Enum

from strategy_python.btc_trend_following_short_5min.state import TrendLegState


class ExitAction(Enum):
    HALF = "half"
    ALL = "all"


def evaluate_exit(
    state: TrendLegState,
    close: float,
    *,
    full_exit_on_first: bool = False,
    ma_slope: float | None = None,
    max_loss_return: float | None = None,
    take_profit_activation_return: float | None = None,
    reverse_cross_signal: int | None = None,
) -> ExitAction | None:
    if not state.invested:
        return None
    if max_loss_return is not None and state.average_entry_price is not None:
        price_change = state.direction * (Decimal(str(close)) - state.average_entry_price)
        if price_change < -state.average_entry_price * Decimal(str(max_loss_return)):
            return ExitAction.ALL
    if ma_slope is not None and state.direction * ma_slope <= 0:
        return ExitAction.ALL
    if take_profit_activation_return is not None and not state.dynamic_exit_armed:
        entry = state.average_entry_price
        if entry is not None and (
            state.direction * (Decimal(str(close)) - entry)
            >= entry * Decimal(str(take_profit_activation_return))
        ):
            state.dynamic_exit_armed = True
        else:
            return None
    if reverse_cross_signal is not None:
        return ExitAction.ALL if reverse_cross_signal == -state.direction else None
    if state.final_exit_level is None or state.first_exit_level is None:
        return None
    final_hit = (
        close <= float(state.final_exit_level)
        if state.direction > 0
        else close
        >= float(
            state.final_exit_level,
        )
    )
    if final_hit:
        return ExitAction.ALL
    first_hit = (
        close <= float(state.first_exit_level)
        if state.direction > 0
        else close
        >= float(
            state.first_exit_level,
        )
    )
    if first_hit and not state.first_exit_done:
        return ExitAction.ALL if full_exit_on_first else ExitAction.HALF
    return None
