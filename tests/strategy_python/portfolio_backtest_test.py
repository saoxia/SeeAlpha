import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.model import Bar, Quantity

from nautilus_support import TestInstrumentProvider
from strategy_python import _backtesting
from strategy_python.portfolio import BAR_TYPE, load_portfolio
from strategy_python.portfolio import create_strategies as _create_strategies
from strategy_python.portfolio_backtest import build_backtest_spec

BACKTEST_SPEC = build_backtest_spec(load_portfolio())


def create_strategies(start, config=None):
    """Build the current strategies for shared-budget regression scenarios."""
    config = config or load_portfolio()
    return _create_strategies(start, config)


@pytest.mark.parametrize("third_member", [False, True])
def test_same_account_trades_and_exports_both_strategies(tmp_path, monkeypatch, third_member):
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    start = datetime(2026, 8, 3, tzinfo=UTC)
    timestamp = int((start - timedelta(days=15)).timestamp() * 1_000_000_000)
    bars = []
    previous = 2000
    for minute in range(18 * 1440):
        price = 2000 + 180 * math.sin(minute / 37) + 90 * math.sin(minute / 151)
        if 3500 <= minute < 3600:
            price *= 0.55
        if 5500 <= minute < 5600:
            price *= 1.8
        now = timestamp + (minute + 1) * 60_000_000_000 - 1_000_000
        bars.append(
            Bar(
                BAR_TYPE,
                instrument.make_price(previous),
                instrument.make_price(max(previous, price) + 3),
                instrument.make_price(min(previous, price) - 3),
                instrument.make_price(price),
                Quantity.from_str("1000.000"),
                now,
                now,
            )
        )
        previous = price
    overrides = {
        "risk_reference_days": 1,
        "risk_move_hours": 4,
        "ma_filter_period": 4,
        "ma_filter_window": 4,
        "recovery_ma_period": 2,
    }
    from dataclasses import replace

    config = load_portfolio()
    config = replace(
        config, members=tuple(replace(m, parameters={} if m.strategy == "mean_reversion_short_5min" else overrides) for m in config.members)
    )
    if third_member:
        first, second = config.members
        config = replace(
            config,
            members=(
                replace(first, risk_weight=Decimal(".4")),
                replace(second, risk_weight=Decimal(".3")),
                replace(
                    first,
                    id="SLOW",
                    risk_weight=Decimal(".3"),
                    parameters={**overrides, "bollinger_period": 10},
                ),
            ),
        )
    strategies, budget = create_strategies(start, config)
    long, short = strategies[:2]
    # Isolate shared-account execution/export from touch-frequency signal selection;
    # the real rolling gate and entry cancellations are covered in entry_touch_test.py.
    monkeypatch.setattr(short.features.entry_filters[5], "frequency_allowed", lambda: True)
    observed = []

    class InspectEngine:
        def __init__(self, *args, **kwargs):
            self.engine = BacktestEngine(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.engine, name)

        def dispose(self):
            if not observed:
                observed.append(len(self.list_venues()))
                assert long.portfolio.account(instrument.id.venue).id == short.portfolio.account(
                    instrument.id.venue
                ).id
                assert long._account_equity() == short._account_equity()
                assert long._account_equity() != Decimal(500)
            self.engine.dispose()

    monkeypatch.setattr(_backtesting, "BacktestEngine", InspectEngine)
    monkeypatch.setattr(_backtesting, "load_binance_data", lambda *a, **kw: (instrument, bars))
    output = _backtesting.run_backtest(
        start,
        start + timedelta(days=3),
        strategies,
        "mean_reversion_portfolio",
        BACKTEST_SPEC,
        report_dir=tmp_path,
        portfolio_parameters=budget.snapshot(),
    )
    assert observed == [1]
    parameters = json.loads((output / "parameters.json").read_text())
    assert parameters["backtest_spec"]["starting_balance"] == "500"
    assert parameters["portfolio"]["total_risk_fraction"] == "0.30"
    assert set(parameters["strategies"]) == {str(strategy.strategy_id) for strategy in strategies}
    account = pd.read_csv(output / "account.csv")
    assert Decimal(str(account.iloc[0]["total"])) == 500
    fills = pd.read_csv(output / "fills.csv")
    positions = pd.read_csv(output / "positions.csv")
    assert set(fills["strategy_id"]) == {str(strategy.strategy_id) for strategy in strategies}
    assert positions.groupby("strategy_id")["entry"].apply(set).to_dict() == {
        str(strategy.strategy_id): {"BUY" if strategy.trade_state.direction == 1 else "SELL"}
        for strategy in strategies
    }
    for name in ("fills", "positions"):
        combined = pd.read_csv(output / f"{name}.csv")
        parts = []
        for index, strategy in enumerate(strategies, start=1):
            part = pd.read_csv(output / f"strategy_{index:02d}_{name}.csv")
            assert set(part["strategy_id"]) == {str(strategy.strategy_id)}
            parts.append(part)
        assert sum(len(part) for part in parts) == len(combined)
    summary = json.loads((output / "summary.json").read_text())
    assert set(summary["by_strategy"]) == {str(strategy.strategy_id) for strategy in strategies}
    # 多空持仓有时间重叠，证明同合约持仓没有被净额抵消。
    longs = positions[positions["entry"] == "BUY"]
    shorts = positions[positions["entry"] == "SELL"]
    assert any(
        max(a.ts_opened, b.ts_opened) < min(a.ts_closed, b.ts_closed)
        for a in longs.itertuples()
        for b in shorts.itertuples()
    )
    html = (output / "report.html").read_text()
    assert "strategy_01_fills.csv" in html and "strategy_02_positions.csv" in html
    assert "组合风险设置" in html


@pytest.mark.parametrize("member", [0, 1])
def test_pending_entry_and_add_keep_budget_until_cancel_confirmation(member):
    strategies, budget = create_strategies(datetime(2026, 8, 1, tzinfo=UTC))
    strategy = strategies[member]
    live = {"entry"}
    strategy._cached_order = lambda order_id: object() if order_id in live else None
    strategy._order_meta["entry"] = SimpleNamespace(role="initial", planned_risk=Decimal(75))
    assert strategy._committed_cycle_risk() == 75
    assert budget.available(strategy.shared_risk_key, Decimal(500)) == 0
    strategy.trade_state.cycle_risk_budget = Decimal(75)
    live.clear()
    assert strategy._committed_cycle_risk() == 75
    # 平仓后仍在等待撤销的加仓单继续占额度。
    strategy.trade_state.clear_cycle()
    live.add("entry")
    strategy._order_meta["entry"] = SimpleNamespace(role="add", planned_risk=Decimal(75))
    assert strategy._committed_cycle_risk() == 75
    live.clear()
    assert strategy._committed_cycle_risk() == 0


@pytest.mark.parametrize("member", [0, 1])
def test_initial_size_is_capped_and_waits_for_pending_cancellation(member):
    strategies, _ = create_strategies(datetime(2026, 8, 1, tzinfo=UTC))
    strategy = strategies[member]
    strategy._account_equity = lambda: Decimal(500)
    strategy._size_increment = lambda: Decimal(".01")
    strategy.signal_model.evaluate = lambda *a: SimpleNamespace(
        direction=1 if member == 0 else -1, price=2000, timestamp_ns=1
    )
    strategy.features.bollinger = (1900., 1950., 2000.)
    if member == 1:
        strategy.features.entry_distance_allowed = lambda price: True
        strategy.features.entry_touch_allowed = lambda now: True
        strategy.features.add_distance_mean = 10.
        strategy.features.stop_ma_distance.current_ma = 2100.
        strategy.features.stop_p995 = 5.
        strategy.features.stop_ma_distance.current_ma = 1950.
        strategy.features.ma_distance.current_ma = 1950.
    strategy.risk_model.size_initial_entry = lambda *a, **kw: {
        "unit_quantity": Decimal(10),
        "risk_per_quantity": Decimal(10),
        "planned_risk": Decimal(100),
        "risk_context": {},
    }
    targets = []
    strategy.apply_entry_target = lambda state, target: targets.append(target)
    strategy._cancel_entry = lambda state: None
    strategy._cached_order = lambda order_id: object()
    strategy._refresh_initial_order(strategy.trade_state, 1)
    assert not targets
    strategy._cached_order = lambda order_id: None
    strategy._refresh_initial_order(strategy.trade_state, 1)
    assert targets[0].quantity == Decimal("7.50")
    assert targets[0].planned_risk == 75


@pytest.mark.parametrize("member", [0, 1])
def test_old_cycle_pending_add_blocks_new_cycle(member):
    strategies, _ = create_strategies(datetime(2026, 8, 1, tzinfo=UTC))
    strategy = strategies[member]
    strategy.trade_state.clear_cycle()
    strategy._cancel_entry = lambda state: None
    strategy._cached_order = lambda order_id: object() if order_id == "old_add" else None
    strategy._order_meta["old_add"] = SimpleNamespace(role="add", planned_risk=Decimal(20), invalidated=False)
    calls = []
    strategy.signal_model.evaluate = lambda *a: calls.append(True)
    strategy._refresh_initial_order(strategy.trade_state, 1)
    assert calls == []


@pytest.mark.parametrize("case", ["empty", "duplicate", "netting", "instrument"])
def test_invalid_portfolio_rejected_before_loading_data(tmp_path, monkeypatch, case):
    from dataclasses import replace

    from nautilus_trader.model import InstrumentId, OmsType

    start = datetime(2026, 8, 1, tzinfo=UTC)
    strategies, _ = create_strategies(start)
    spec = BACKTEST_SPEC
    if case == "empty":
        strategies = []
    elif case == "duplicate":
        strategies = [strategies[0], strategies[0]]
    elif case == "netting":
        spec = replace(spec, oms_type=OmsType.NETTING)
    else:
        spec = replace(spec, instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"))
    monkeypatch.setattr(
        _backtesting, "load_binance_data", lambda *a, **kw: pytest.fail("不应读取行情")
    )
    with pytest.raises(ValueError):
        _backtesting.run_backtest(
            start, start + timedelta(days=1), strategies, "invalid", spec, report_dir=tmp_path
        )


@pytest.mark.parametrize("member_count", [1, 3])
def test_manifest_entry_persists_configuration_and_member_reports(
    tmp_path, monkeypatch, member_count
):
    from strategy_python import portfolio_backtest
    from tests.strategy_python.portfolio_test import default_document

    document = default_document()
    document["name"] = "configured_run"
    document["account"]["starting_balance"] = "777"
    if member_count == 1:
        document["strategies"][0]["risk_weight"] = "1"
        document["strategies"][1]["enabled"] = False
    else:
        document["risk"] = {"mode": "none"}
        for member in document["strategies"]:
            member.pop("risk_weight")
        document["strategies"].append({"id": "TREND", "strategy": "trend_following_long_5min"})
    config_path = tmp_path / "run.json"
    config_path.write_text(json.dumps(document))
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    start = datetime(2026, 8, 1, tzinfo=UTC)
    now = int(start.timestamp() * 1_000_000_000)
    price = instrument.make_price(2000)
    bars = [Bar(BAR_TYPE, price, price, price, price, Quantity.from_str("1.000"), now, now)]
    monkeypatch.setattr(_backtesting, "load_binance_data", lambda *a, **kw: (instrument, bars))
    requested = []

    def run(*args, **kwargs):
        requested.append((args, kwargs.copy()))
        kwargs["report_dir"] = tmp_path / "reports"
        return _backtesting.run_backtest(*args, **kwargs)

    monkeypatch.setattr(portfolio_backtest, "run_engine_backtest", run)
    output = portfolio_backtest.run_backtest(
        start, start + timedelta(days=1), config_path=config_path, catalog_only=True
    )
    parameters = json.loads((output / "parameters.json").read_text())
    assert parameters["portfolio"]["run_config"]["name"] == "configured_run"
    assert len(parameters["strategies"]) == member_count
    assert parameters["backtest_spec"]["starting_balance"] == "777"
    assert not parameters["backtest_spec"]["prepare_data_before_run"]
    assert parameters["portfolio"]["risk_mode"] == ("shared" if member_count == 1 else "none")
    assert requested[0][1]["report_dir"].parts[-3:] == ("backtest", "portfolio", "configured_run")
    assert len(list(output.glob("strategy_*_positions.csv"))) == member_count
