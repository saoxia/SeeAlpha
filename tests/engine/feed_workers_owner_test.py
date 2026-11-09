"""Only Engine owns live feed/history worker processes and option batches."""

import json

from engine.data_manager.contracts import DataPolicy
from engine.data_manager.service import DataService


class Worker:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode


def engine_manager(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    manager = DataService("42", project=tmp_path, state_root=tmp_path / "state")
    commands, stopped = [], []

    def launch(command, log_name, access=None):
        worker = Worker()
        commands.append((command, log_name, access, worker))
        return worker

    def stop(worker):
        worker.returncode = 0
        stopped.append(worker)

    monkeypatch.setattr(manager.subscriptions, "_launch", launch)
    monkeypatch.setattr(manager, "_stop", stop)
    return manager, commands, stopped


def test_engine_only_starts_and_stops_futures_feed_and_history(tmp_path, monkeypatch):
    manager, commands, stopped = engine_manager(tmp_path, monkeypatch)
    policy = DataPolicy(
        symbol="ETHUSDT", base_level="1m",
        realtime_enabled=True, history_enabled=True,
        history_mode="start", history_start="2026-08-01T00:00:00Z",
    )
    manager.subscriptions._write([policy])
    manager.subscriptions.restore()
    assert len(commands) == 3
    assert {row[1] for row in commands} == {
        "ETHUSDT-collection.log", "ETHUSDT-calculation.log", "ETHUSDT-history.log",
    }
    assert all(row[2] == ("ETHUSDT", "futures_um", True) for row in commands)
    collection = manager.subscriptions.processes[("futures_um", "ETHUSDT")]
    manager.subscriptions.restore()
    assert len(commands) == 3  # A policy refresh never duplicates active workers.
    disabled = policy.model_copy(update={"realtime_enabled": False, "history_enabled": False})
    manager.subscriptions._write([disabled])
    manager.subscriptions.restore()
    assert collection in stopped
    assert not manager.subscriptions.processes and not manager.subscriptions.auxiliary
    manager.close()


def test_engine_options_use_two_shared_workers_and_durable_manifest(tmp_path, monkeypatch):
    manager, commands, stopped = engine_manager(tmp_path, monkeypatch)
    scope = DataPolicy(product="options", symbol="ALL", realtime_enabled=True, history_enabled=True)
    count = 1514
    contracts = [
        DataPolicy(
            product="options", symbol=f"BTC-261225-{10000 + i}-C",
            underlying="BTCUSDT", realtime_enabled=True, history_enabled=True,
        )
        for i in range(count)
    ]
    manager.subscriptions._write([
        scope, DataPolicy(product="options", symbol="BTCUSDT"), *contracts,
    ])
    manager.subscriptions.restore()
    assert len(commands) == 2
    assert "engine.feed.managed" in commands[0][0]
    assert "engine.pipeline.pair_data" in commands[1][0]
    assert all("--options-config" in args for args, *_ in commands)
    assert len(json.loads((manager.directory / "options-runtime.json").read_text())["policies"]) == count
    assert set(manager.subscriptions.processes) == {("options", "ALL")}
    assert set(manager.subscriptions.auxiliary) == {("options", "ALL", "history")}
    manager.subscriptions.restore()
    assert len(commands) == 2

    # A new contract only changes the Engine-owned manifest, not worker count.
    extra = DataPolicy(
        product="options", symbol="BTC-270129-20000-P",
        underlying="BTCUSDT", realtime_enabled=True, history_enabled=True,
    )
    manager.subscriptions._write([scope, *contracts, extra])
    manager.subscriptions.restore()
    assert len(commands) == 2
    assert len(json.loads((manager.directory / "options-runtime.json").read_text())["policies"]) == count + 1

    manager.subscriptions.pause_for_deletion(contracts[0].symbol, "options")
    assert len(commands) == 2
    assert stopped == []  # Other option contracts keep the shared workers.
    rows = manager.subscriptions._read()
    selected = next(p for p in rows if p.symbol == contracts[0].symbol)
    assert not selected.realtime_enabled and not selected.history_enabled
    assert next(p for p in rows if p.symbol == extra.symbol).realtime_enabled

    disabled = [p.model_copy(update={"realtime_enabled": False, "history_enabled": False})
                for p in rows]
    manager.subscriptions._write(disabled)
    manager.subscriptions.restore()
    assert len(stopped) == 2
    assert not manager.subscriptions.processes
    assert not manager.subscriptions.auxiliary
    manager.close()
