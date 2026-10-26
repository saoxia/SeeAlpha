"""Cash-flow equity includes unrealized PnL and both trade directions."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from strategy_python._research.four_strategy_20260914.research import measure


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "side,opposite,exit_px,pnl", [("BUY", "SELL", 110, 9.8), ("SELL", "BUY", 90, 9.8)]
)
def test_equity_reconciles_signed_cashflows(tmp_path, side, opposite, exit_px, pnl, streaming):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    times = [start + timedelta(minutes=i, seconds=59, milliseconds=999) for i in range(3)]
    fills = pd.DataFrame(
        {
            "status": ["FILLED"] * 2,
            "side": [side, opposite],
            "filled_qty": [1.0, 1.0],
            "avg_px": [100.0, exit_px],
            "commissions": [["0.1 USDT"], ["0.1 USDT"]],
            "ts_last": [times[0], times[2]],
        }
    )
    trader = SimpleNamespace(
        generate_order_fills_report=lambda: fills,
        generate_positions_report=lambda: pd.DataFrame({"realized_pnl": [f"{pnl} USDT"]}),
        generate_account_report=lambda venue: pd.DataFrame(),
    )
    cache = SimpleNamespace(
        account_for_venue=lambda venue: SimpleNamespace(
            balance_total=lambda currency: SimpleNamespace(as_double=lambda: 500 + pnl)
        )
    )
    adverse_px = 80 if side == "BUY" else 120
    bars = [
        SimpleNamespace(close=p, ts_event=int(pd.Timestamp(t).value))
        for t, p in zip(times, [100, adverse_px, exit_px])
    ]
    if streaming:
        from strategy_python._backtesting import BarStream

        bars = BarStream([value] for value in bars)
    result = measure(
        SimpleNamespace(**vars(trader), cache=cache),
        bars,
        start,
        start + timedelta(minutes=3),
        tmp_path,
    )
    assert result["net_pnl"] == pytest.approx(9.8)
    assert result["max_drawdown_pct"] == pytest.approx(4.02)
    assert result["stress"]["2"]["net_pnl"] == pytest.approx(9.8 - (100 + exit_px) * 0.0002)
