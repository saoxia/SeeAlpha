from decimal import Decimal

import pytest
from nautilus_trader.model import BarType, InstrumentId

from strategy_python.mean_reversion_short_v2_5min import (
    MeanReversionShortV2Config,
    MeanReversionShortV2Strategy,
)
from strategy_python.mean_reversion_short_v2_5min.risk import quantity_for_risk


@pytest.mark.parametrize("leverage", [3, 5])
def test_notional_cap_reserves_margin_and_entry_cost(leverage):
    equity = Decimal(500)
    entry = Decimal(2000)
    stop = Decimal(2008)
    qty = quantity_for_risk(
        equity,
        entry,
        stop,
        risk_fraction=0.05,
        max_notional_fraction=leverage,
        fee_buffer=0.0007,
        increment=Decimal(".001"),
    )
    assert qty * entry <= equity * leverage
    assert qty * entry / Decimal(leverage) + qty * entry * Decimal(".0007") <= equity
    assert qty * (stop - entry + (entry + stop) * Decimal(".0007")) <= equity * Decimal(".05")
    assert qty * entry / equity > Decimal(leverage) * Decimal(".99")


@pytest.mark.parametrize(
    "parameters",
    [
        {"max_notional_fraction": 6.0},
        {"max_notional_fraction": float("inf")},
        {"risk_fraction": 0.1},
        {"boll_period": 0},
        {"max_holding_hours": 0},
    ],
)
def test_invalid_risk_and_window_config_rejected(parameters):
    with pytest.raises((ValueError, TypeError)):
        MeanReversionShortV2Strategy(
            MeanReversionShortV2Config(
                instrument_id=InstrumentId.from_str("ETHUSDT-PERP.BINANCE"),
                bar_type=BarType.from_str("ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"),
                **parameters,
            )
        )


@pytest.mark.parametrize("leverage", [3, 4, 5])
def test_backtest_wires_leverage_to_margin_and_position_cap(leverage, monkeypatch):
    from datetime import UTC, datetime

    from strategy_python._backtesting import StrategyBacktest
    from strategy_python.mean_reversion_short_v2_5min.backtest import run_backtest

    seen = {}

    def run(self, start, end, **kwargs):
        seen.update(spec=self.spec, parameters=kwargs)

    monkeypatch.setattr(StrategyBacktest, "run", run)
    run_backtest(
        datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC), max_leverage=leverage
    )
    assert seen["spec"].leverage == Decimal(leverage)
    assert seen["parameters"]["max_notional_fraction"] == leverage
