"""Audit-only ETH MRL replay: unchanged native strategy and data, order-price MIN_NOTIONAL."""

from .native import (
    ROOT,
    START,
    END,
    REFERENCES,
    verify,
    strategy_for,
    BacktestSpec,
    Decimal,
    OmsType,
    ParquetDataCatalog,
    load_settings,
    json_write,
    digest,
    json,
    BacktestEngine,
    BacktestEngineConfig,
    LoggingConfig,
    time,
    BINANCE_VENUE,
    AccountType,
    Money,
    timedelta,
    load_binance_data,
    pd,
    measure,
    opening_order_allowed,
    Counter,
)


def verify_amendment():
    manifest = json.loads((ROOT / "audit_amendment/manifest.json").read_text())
    for name, sha in manifest["sha256"].items():
        assert digest(ROOT / name) == sha, name


def execute(run_id="ETH_MRL"):
    assert run_id == "ETH_MRL"
    verify_amendment()
    verify()
    asset, kind = run_id.split("_")
    output = ROOT / "runs" / run_id
    output.mkdir(parents=True, exist_ok=True)
    if (output / "completion.json").exists():
        for name, expected in json.loads((output / "completion.json").read_text()).items():
            assert digest(output / name) == expected
        print("REUSED", run_id, flush=True)
        return
    strategy = strategy_for(asset, kind)
    spec = BacktestSpec(
        instrument_id=strategy.config.instrument_id,
        data_symbol=asset + "USDT",
        bar_type=strategy.config.bar_type,
        interval="1m",
        warmup_days=135 if kind == "MRL" else 21,
        oms_type=OmsType.HEDGING,
        starting_balance=Decimal(500),
        leverage=Decimal(5),
        prepare_data_before_run=False,
    )
    catalog = ParquetDataCatalog(str(load_settings().catalog_dir))
    instrument = max(
        catalog.instruments(instrument_ids=[str(spec.instrument_id)]), key=lambda x: x.ts_init
    )
    json_write(
        output / "parameters.json",
        {
            "manifest_sha256": digest(ROOT / "manifest.json"),
            "config": json.loads(strategy.config.json()),
            "instrument": instrument.to_dict(instrument),
            "warmup_days": spec.warmup_days,
            "starting_balance": 500,
            "leverage": 5,
        },
    )
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    market = []
    began = time.monotonic()
    try:
        engine.add_venue(
            venue=BINANCE_VENUE,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=[Money(500, instrument.quote_currency)],
            default_leverage=Decimal(5),
        )
        engine.add_instrument(instrument)
        engine.add_strategy(strategy)
        cursor = START - timedelta(days=spec.warmup_days)
        while cursor < END:
            stop = min(cursor + timedelta(days=90), END)
            _, bars = load_binance_data(cursor, stop, spec, check_data=False)
            if bars:
                market.append(
                    pd.DataFrame(
                        {
                            "ts": [b.ts_event for b in bars],
                            "open": [float(b.open) for b in bars],
                            "high": [float(b.high) for b in bars],
                            "low": [float(b.low) for b in bars],
                            "close": [float(b.close) for b in bars],
                        }
                    )
                )
                engine.add_data(bars)
                engine.run(streaming=True)
                engine.clear_data()
            cursor = stop
            print("BATCH", run_id, stop.isoformat(), flush=True)
        engine.end()
        frame = pd.concat(market, ignore_index=True)
        assert frame.ts.is_monotonic_increasing and frame.ts.is_unique
        if kind == "MRL":
            frame.to_parquet(output / "market.parquet", index=False)
        result = measure(
            engine,
            pd.Series(frame.close.to_numpy(), index=pd.to_datetime(frame.ts, utc=True)),
            START,
            END,
            output,
            500,
        )
        pd.DataFrame(strategy.observations).to_parquet(output / "decisions.parquet", index=False)
        fills = engine.trader.generate_order_fills_report()
        entries = fills[~fills.is_reduce_only.astype(bool)]
        invalid = sum(
            not opening_order_allowed(
                instrument, Decimal(str(f.filled_qty)), Decimal(str(f.avg_px))
            )
            for f in entries.itertuples()
        )
        filled_below_bounds = invalid
        exceptions = []
        invalid_orders = 0
        for f in entries.itertuples():
            quantity = Decimal(str(f.filled_qty))
            fill_price = Decimal(str(f.avg_px))
            fill_allowed = opening_order_allowed(instrument, quantity, fill_price)
            if str(f.type) == "LIMIT":
                order_price = Decimal(str(f.price))
                order_allowed = opening_order_allowed(
                    instrument, Decimal(str(f.quantity)), order_price
                )
                invalid_orders += not order_allowed
                if not fill_allowed:
                    assert order_allowed and f.side == "BUY" and fill_price <= order_price
                    assert quantity == Decimal(str(f.quantity))
                    exceptions.append(
                        {
                            "position_id": str(f.position_id),
                            "quantity": str(quantity),
                            "order_price": str(order_price),
                            "fill_price": str(fill_price),
                            "order_notional": str(quantity * order_price),
                            "fill_notional": str(quantity * fill_price),
                        }
                    )
            else:
                invalid_orders += not fill_allowed
        assert invalid_orders == 0 and not engine.cache.positions_open()
        invalid = invalid_orders
        result["fills_below_minimum_after_limit_price_improvement"] = exceptions
        result["fill_price_bounds_exception_count"] = filled_below_bounds
        result["audit_amendment_sha256"] = digest(ROOT / "audit_amendment/manifest.json")
        first = ROOT / "audit_amendment/first_ETH_MRL"
        pd.testing.assert_frame_equal(
            pd.read_csv(first / "trades.csv"),
            pd.read_csv(output / "trades.csv"),
            check_exact=False,
            rtol=1e-10,
            atol=1e-9,
        )
        cols = ["type", "side", "filled_qty", "avg_px", "commissions", "is_reduce_only", "ts_last"]
        pd.testing.assert_frame_equal(
            pd.read_csv(first / "fills.csv")[cols],
            pd.read_csv(output / "fills.csv")[cols],
            check_exact=False,
            rtol=1e-10,
            atol=1e-9,
        )
        pd.testing.assert_frame_equal(
            pd.read_parquet(first / "decisions.parquet"),
            pd.read_parquet(output / "decisions.parquet"),
        )
        result["audit_replay_matches_first_attempt"] = True
        reasons = Counter()
        off_tick = 0
        for order in engine.cache.orders():
            for field in ("price", "trigger_price"):
                value = getattr(order, field, None)
                if (
                    value is not None
                    and value.as_decimal() % instrument.price_increment.as_decimal()
                ):
                    off_tick += 1
            for event in order.events:
                if reason := getattr(event, "reason", None):
                    reasons[type(event).__name__ + ": " + str(reason)] += 1
        assert off_tick == 0
        if run_id in REFERENCES:
            old = pd.read_csv(REFERENCES[run_id] / "trades.csv")
            new = pd.read_csv(output / "trades.csv")
            pd.testing.assert_frame_equal(old, new, check_exact=False, rtol=1e-10, atol=1e-9)
            old_f = pd.read_csv(REFERENCES[run_id] / "fills.csv")
            new_f = pd.read_csv(output / "fills.csv")
            cols = [
                "type",
                "side",
                "filled_qty",
                "avg_px",
                "commissions",
                "is_reduce_only",
                "ts_last",
            ]
            pd.testing.assert_frame_equal(
                old_f[cols], new_f[cols], check_exact=False, rtol=1e-10, atol=1e-9
            )
            result["baseline_trade_and_fill_reconciliation"] = True
        result.update(
            entry_fills_outside_constraints=invalid,
            off_tick_order_price_fields=off_tick,
            order_event_reasons=dict(reasons),
            observations=len(strategy.observations),
            elapsed_seconds=time.monotonic() - began,
        )
        verify()
        json_write(output / "metrics.json", result)
        json_write(
            output / "completion.json",
            {p.name: digest(p) for p in sorted(output.iterdir()) if p.name != "completion.json"},
        )
        print("DONE", run_id, result["net_pnl"], result["trades"], flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    execute()
