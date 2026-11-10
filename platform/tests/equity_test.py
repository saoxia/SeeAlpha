"""Valuation accounting, persistence/replay, failure recovery and HTTP boundaries."""

import asyncio
import copy
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.db.models import EquityEvent, EquityPoint
from api.db.orm import ORMDatabase
from api.equity.repository import EquityRepository
from api.equity.router import aligned_points, candles
from api.equity.service import EquityService
from api.equity.valuation import reconcile, value
from api.main import create_app
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]


def signin(client, username="admin"):
    assert client.post(
        "/api/auth/login",
        json={"username": username, "password": "test-password-123"},
    ).status_code == 200


def create(client, name="main"):
    return client.post("/api/system/exchange-accounts", json={
        "exchange": "binance",
        "environment": "production",
        "account_name": name,
        "api_key": "fixture-key-1234",
        "secret": "fixture-secret-5678",
    })



NOW = 1_800_000_000_000


def evidence(stamp=NOW, wallet="100", mark="2010", quantity="0.01", entry="2000"):
    return {"time": stamp, "reason": "update", "state": {
        "wallet": {"USDT": wallet}, "fx": {},
        "positions": [{"id": "long", "instrument": "ETHUSDT-PERP.BINANCE", "currency": "USDT",
                       "quantity": quantity, "entry": entry}],
        "marks": {"ETHUSDT-PERP.BINANCE": {"price": mark, "time": stamp}},
    }}


@pytest.fixture
def database(tmp_path):
    url = f"sqlite:///{tmp_path / 'equity.db'}"
    config = Config(str(PROJECT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT / "migrations"))
    config.attributes.update(database_url=url)
    command.upgrade(config, "head")
    db = ORMDatabase(url, tmp_path / "reference.db")
    db.initialize()
    yield db
    db.close()


def test_equity_changes_without_a_trade_and_uses_wallet_as_single_cash_authority():
    assert value(evidence())["total_equity"] == "100.10"
    assert value(evidence(mark="2020"))["total_equity"] == "100.20"
    # A funding/fee wallet update is counted once, not again from ledger cashflows.
    assert value(evidence(wallet="99", mark="2020"))["total_equity"] == "99.20"
    assert value(evidence(quantity="-0.02"))["total_equity"] == "99.80"
    mixed = evidence(wallet="100.00000001")
    mixed["state"]["wallet"]["BNB"] = "-0.001"
    mixed["state"]["fx"]["BNB"] = {"price": "1000", "time": NOW}
    assert value(mixed)["total_equity"] == "99.10000001"
    mixed["state"]["positions"].append({**mixed["state"]["positions"][0], "id": "short", "quantity": "-0.02"})
    assert value(mixed)["total_equity"] == "98.90000001"


@pytest.mark.parametrize("change", ["missing_mark", "stale_mark", "missing_fx", "stale_fx", "stale_account", "nan", "gap"])
def test_incomplete_evidence_never_fabricates_zero(change):
    event = evidence()
    if change == "missing_mark":
        event["state"]["marks"].clear()
    elif change == "stale_mark":
        event["state"]["marks"]["ETHUSDT-PERP.BINANCE"]["time"] -= 30_001
    elif change in {"missing_fx", "stale_fx"}:
        event["state"]["wallet"]["USDC"] = "1"
        if change == "stale_fx":
            event["state"]["fx"]["USDC"] = {"price": "1", "time": NOW - 120_001}
    elif change == "stale_account":
        event["state"]["account_time"] = NOW - 15_001
    elif change == "nan":
        event["state"]["wallet"]["USDT"] = "NaN"
    else:
        event["reason"] = "gap"
    point = value(event)
    assert point["total_equity"] is None and point["gap"] and point["error"]


def test_reconciliation_uses_common_prices_and_checks_structural_and_repeat_thresholds():
    initial = evidence(quantity="1")
    renamed = copy.deepcopy(initial)
    renamed["state"]["positions"][0]["id"] = "reconciled-new-id"
    assert reconcile(initial, renamed)["calibrate"] is False
    assert reconcile(initial, evidence(wallet="99", quantity="1"))["calibrate"] is True
    assert reconcile(initial, evidence(quantity="2"))["structural"] is True
    # Same position quantity, cost-basis drift: > max(1 USDT, 0.05%) twice.
    observed = evidence(quantity="1", entry="1998")
    first = reconcile(initial, observed)
    assert first["difference"] == "2" and first["threshold"] == "1"
    assert first["breach"] and not first["calibrate"]
    assert reconcile(initial, observed, True)["calibrate"]
    assert not reconcile(initial, evidence(quantity="1", entry="1999"), True)["calibrate"]


def test_atomic_idempotent_events_environment_isolation_and_replay(database):
    repository = EquityRepository(database)
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(lambda _: repository.append(1, "production", "same", evidence()), range(3)))
    assert len(set(ids)) == 1
    with pytest.raises(ValueError, match="冲突"):
        repository.append(1, "production", "same", evidence(wallet="999"))
    repository.append(1, "sandbox", "sandbox", evidence(wallet="200"))
    repository.append(1, "production", "next", evidence(stamp=NOW + 1, mark="2020"))
    with database.session() as session:
        raw_before = [(event.id, copy.deepcopy(event.body)) for event in session.scalars(select(EquityEvent))]
    with database.session(write=True) as session:
        point = session.get(EquityPoint, ids[0])
        point.body = {**point.body, "total_equity": "9999"}
    job = repository.create_rebuild(1, "production", None, NOW + 2)
    with pytest.raises(Exception, match="已有资产重算"):
        repository.create_rebuild(1, "production", None, NOW + 2)
    repository.rebuild(job["id"])
    assert repository.job(job["id"])["processed"] == 2
    production = list(repository.points([(1, "production")], NOW, NOW + 2))
    sandbox = list(repository.points([(1, "sandbox")], NOW, NOW + 2))
    assert production[0][2]["total_equity"] == "100.10"
    assert sandbox[0][2]["total_equity"] == "200.10"
    with database.session() as session:
        assert raw_before == [(event.id, event.body) for event in session.scalars(select(EquityEvent))]


def test_replay_failure_rolls_back_all_points_and_preserves_raw(database, monkeypatch):
    repository = EquityRepository(database)
    for index in range(2):
        repository.append(1, "production", str(index), evidence(stamp=NOW + index))
    original = list(repository.points([(1, "production")], NOW, NOW + 2))
    job = repository.create_rebuild(1, "production", None, NOW + 2)
    calls = []

    def fail_second(event):
        calls.append(event)
        if len(calls) == 2:
            raise RuntimeError("fixture failure")
        return {**value(event), "total_equity": "999"}

    monkeypatch.setattr("api.equity.repository.value", fail_second)
    with pytest.raises(RuntimeError, match="fixture failure"):
        repository.rebuild(job["id"])
    assert repository.job(job["id"])["status"] == "failed"
    assert list(repository.points([(1, "production")], NOW, NOW + 2)) == original
    assert repository.create_rebuild(1, "production", None, NOW + 2)["status"] == "queued"
    repository.recover()
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(EquityEvent)) == 2


def test_candles_preserve_extrema_and_exclude_gap_buckets():
    points = [(NOW + i, i, {"total_equity": n}) for i, n in enumerate(["100", "110", "90", "105"])]
    rows, gaps = candles(points, 300, 10)
    assert gaps == []
    assert (rows[0]["open"], rows[0]["high"], rows[0]["low"], rows[0]["close"]) == ("100", "110", "90", "105")
    points.append((NOW + 5, 5, {"total_equity": None}))
    assert candles(points, 300, 10) == ([], [NOW // 300_000 * 300_000])


def test_owner_lock_recovery_and_restart(database, tmp_path):
    async def scenario():
        accounts = SimpleNamespace(repository=SimpleNamespace(list_exchange_accounts=list))
        first, second = EquityService(database, accounts), EquityService(database, accounts)
        job = first.repository.create_rebuild(1, "production", None, NOW)
        path = tmp_path / "owner.lock"
        await first.start(path)
        assert first.repository.job(job["id"])["status"] == "interrupted"
        await second.start(path)
        assert first.owner and second.owner is None
        with pytest.raises(Exception, match="未持有"):
            await second.rebuild({"id": 1, "environment": "production"}, None, NOW)
        await first.close()
        await second.start(path)
        assert second.owner
        await second.close()
    asyncio.run(scenario())


@pytest.mark.usefixtures("platform_engine_bootstrap")
def test_http_auth_real_candles_rebuild_and_environment_isolation(database, tmp_path):
    for username in ("admin", "viewer"):
        auth_repository(database).create_user(username, hash_password("test-password-123"))
    settings = Settings(tmp_path / "reference.db", database_url=str(database.engine.url), cookie_secure=False)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/exchange-accounts/equity-candles").status_code == 401
        signin(client)
        account_id = create(client).json()["id"]
        now = int(time.time() * 1000)
        repository = app.state.equity.repository
        repository.append(account_id, "production", "first", evidence(stamp=now))
        path = f"/api/exchange-accounts/{account_id}"
        chart = client.get(path + "/equity-candles").json()
        assert chart["is_sample"] is False and chart["currency"] == "USDT" and chart["status"] == "ready"
        assert chart["candles"][0]["close"] == "100.10"
        assert client.get(path + "/equity-status").json()["latest"]["total_equity"] == "100.10"
        assert client.get("/api/exchange-accounts/999/equity-candles").status_code == 404
        signin(client, "viewer")
        assert client.post(path + "/equity-rebuilds", json={}).status_code == 403
        signin(client)
        assert client.post(path + "/equity-rebuilds", json={}, headers={"Origin": "https://evil.invalid"}).status_code == 403
        assert client.post(path + "/equity-rebuilds", json={"start_ms": now, "end_ms": now - 1}).status_code == 422
        assert client.post(path + "/equity-rebuilds", json={}).status_code == 409
        # Own the same production path without starting collectors in this fixture.
        app.state.equity.owner = (tmp_path / "fake-owner").open("w")
        job = client.post(path + "/equity-rebuilds", json={}).json()
        for _ in range(50):
            completed = client.get(path + f"/equity-rebuilds/{job['id']}").json()
            if completed["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert completed["status"] == "success" and completed["processed"] == 1
        assert client.get(f"/api/exchange-accounts/999/equity-rebuilds/{job['id']}").status_code == 404
        second = create(client, "sandbox").json()["id"]
        old = app.state.accounts.row(second)
        app.state.accounts.repository.update_exchange_account(second, account_name="sandbox", environment="sandbox",
                                                             credentials_encrypted=old["credentials_encrypted"], market_symbols_json="[]")
        repository.append(second, "sandbox", "sandbox", evidence(stamp=now, wallet="500"))
        overview = client.get("/api/exchange-accounts/equity-candles").json()
        assert overview["candles"] == [] and "测试" in overview["warnings"][0]
        assert client.get(path + "/equity-candles").json()["candles"][0]["close"] == "100.10"


def test_collector_persists_wallet_and_fx_events_and_invalidates_on_shutdown(database):

    async def scenario():
        row = {"id": 1, "environment": "production", "credentials_encrypted": "cipher"}
        accounts = SimpleNamespace(private=lambda _: {**row, "credentials": {"apiKey": "fixture", "secret": "different"}})
        service = EquityService(database, accounts)
        finished = asyncio.Event()
        now = int(time.time() * 1000)
        prices = []

        async def price(env, currency):
            prices.append((env, currency))
            return {"price": "600" if currency == "BNB" else "1", "priced_at": now // 1000}

        async def events(_):
            for index, wallet in enumerate(("100", "99")):
                yield {"time": now + index, "reason": "baseline" if index == 0 else "update",
                       "wallet": {"USDT": wallet, "BNB": "0.01"}, "positions": [], "marks": {}}
            finished.set()
            await asyncio.Future()

        service.engine = SimpleNamespace(valuation_events=events, asset_price=price)
        task = asyncio.create_task(service.collect(row))
        await asyncio.wait_for(finished.wait(), 3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        points = list(service.repository.points([(1, "production")], now - 1000, now + 10_000))
        assert [p[2]["total_equity"] for p in points if p[2]["total_equity"] is not None] == ["106.00", "105.00"]
        assert len(prices) == 2
        assert service.repository.state(1, "production")["status"] == "gap"
    asyncio.run(scenario())


def test_collector_repeated_drift_records_calibration_and_restarts_baseline(database, monkeypatch):
    from api.equity import service as module

    async def scenario():
        row = {"id": 1, "environment": "production", "credentials_encrypted": "cipher"}
        service = EquityService(database, SimpleNamespace(private=lambda _: row))
        clock = [1000]
        now = int(time.time() * 1000)
        monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: now / 1000, monotonic=lambda: clock[0]))
        checks = []

        async def snapshot(_, *, positions):
            assert positions is True
            checks.append(True)
            return {"assets": {"USDT": {"walletBalance": "100"}}, "positions": [{
                "id": "new-native-id", "instrument": "ETHUSDT-PERP.BINANCE", "currency": "USDT",
                "quantity": "1", "entry": "1998"}], "observed_at": now // 1000}


        async def events(_):
            for index in range(3):
                event = evidence(stamp=now + index, quantity="1")
                if index:
                    clock[0] += 301
                yield {"time": event["time"], "reason": "update", **{key: val for key, val in event["state"].items() if key != "fx"}}
            await asyncio.Future()

        service.engine = SimpleNamespace(valuation_events=events, asset_account=snapshot)
        task = asyncio.create_task(service.collect(row))
        try:
            for _ in range(100):
                state = service.repository.state(1, "production")
                if state.get("reconciliation", {}).get("calibrate"):
                    break
                await asyncio.sleep(0.01)
            assert len(checks) == 2
            assert state["reconciliation"]["difference"] == "2"
            assert state["reconciliation"]["calibrate"] is True
            with database.session() as session:
                assert any(e.body["reason"] == "calibration" for e in session.scalars(select(EquityEvent)))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_overview_aligns_event_values_instead_of_adding_independent_ohlc():
    timeline = [(NOW + offset, index, {"account_id": account, "total_equity": amount})
                for index, (offset, account, amount) in enumerate([
                    (-2, 1, "100"), (-1, 2, "100"), (0, 1, "110"),
                    (1, 1, "100"), (2, 2, "110"), (3, 2, "100"), (31_004, 1, "100"),
                ])]
    aligned = list(aligned_points(iter(timeline), 2, NOW))
    assert [point[2]["total_equity"] for point in aligned] == ["210", "200", "210", "200", None]
    rows, _ = candles(aligned[:-1], 300, 5)
    assert rows[0]["high"] == "210"  # Independent highs would incorrectly produce 220.
