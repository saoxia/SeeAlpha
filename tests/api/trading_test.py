"""Manual trading boundaries: permissions, idempotency, failures and durable recovery."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.exchanges.trading import ManualTrading
from api.main import create_app
from engine.connectors.binance import manual
from engine.connectors.binance.queries import QueryError
from tests.api.exchanges_test import create, signin

# Legacy app-lifespan regressions opt in to the in-process Engine fixture.
pytestmark = pytest.mark.usefixtures("engine_http")

@pytest.fixture
def trading(tmp_path, monkeypatch):
    settings = Settings(tmp_path / 'api.db', cookie_secure=False)
    db = Database(settings.database_path)
    db.initialize()
    users = AuthRepository(db)
    for name in ('admin', 'viewer'):
        users.create_user(name, hash_password('test-password-123'))
    monkeypatch.setattr("api.exchanges.trading.account_revision", lambda account: "f" * 64)
    app = create_app(settings)
    calls = []

    async def execute(account, symbol, command):
        calls.append((account['environment'], symbol, command))
        key = command.get('client_order_id', 'external-order')
        order = {'client_order_id': key, 'status': 'accepted', 'open': True,
                 'symbol': symbol, 'timestamp': 1000, 'order_id': '123', 'side': 'buy',
                 'order_type': 'limit', 'quantity': '0.01', 'filled': '0', 'price': '2000',
                 'trigger_price': None, 'reduce_only': False}
        return {'position_mode': 'oneway', 'leverage': 20, 'result': order, 'orders': [order], 'positions': [], 'balances': {},
                'instrument': {'symbol': symbol, 'price_increment': '0.01', 'size_increment': '0.001',
                               'min_quantity': '0.001', 'min_notional': '10'}, 'records': [], 'checked_at': 1}
    monkeypatch.setattr(manual, 'execute', execute)
    return app, calls


def payload(**values):
    return {'account_revision': 'f' * 64, 'request_id': str(uuid4()), 'symbol': 'ETHUSDT-PERP', 'environment': 'production',
            'side': 'buy', 'order_type': 'limit', 'quantity': '0.01', 'price': '2000', **values}


def test_trading_permissions_origin_and_no_secret_echo(trading):
    app, calls = trading
    with TestClient(app) as client:
        body = payload()
        route = '/api/trading/accounts/1/orders'
        assert client.post(route, json=body).status_code == 401
        signin(client, 'viewer')
        assert client.post(route, json=body).status_code == 403
        signin(client)
        create(client)
        assert client.post(route, json=body, headers={'Origin': 'https://evil.invalid'}).status_code == 403
        result = client.post(route, json=body)
        assert result.status_code == 200
        assert result.json()['status'] == 'completed'
        assert 'fixture-key' not in result.text and 'fixture-secret' not in result.text
        assert len(calls) == 1


def test_idempotency_survives_service_restart_and_rejects_changed_payload(trading):
    app, calls = trading
    with TestClient(app) as client:
        signin(client)
        create(client)
        body = payload()
        route = '/api/trading/accounts/1/orders'
        first = client.post(route, json=body)
        previous = app.state.trading
        app.state.trading = ManualTrading(app.state.accounts, app.state.exchanges,
                                         app.state.operations, previous.root, engine=app.state.engine)
        assert client.post(route, json=body).json() == first.json()
        assert len(calls) == 1
        assert client.post(route, json={**body, 'quantity': '0.02'}).status_code == 409
        assert len(calls) == 1
        lookup = client.get(f"/api/trading/accounts/1/requests/{body['request_id']}?environment=production")
        assert lookup.json()['status'] == 'completed'


def test_unknown_result_is_reserved_and_never_automatically_resent(trading, monkeypatch):
    app, calls = trading
    async def fail(account, symbol, command):
        calls.append(command)
        raise QueryError('timeout')
    monkeypatch.setattr(manual, 'execute', fail)
    with TestClient(app) as client:
        signin(client)
        create(client)
        body = payload()
        route = '/api/trading/accounts/1/orders'
        first = client.post(route, json=body)
        assert first.status_code == 200 and first.json()['status'] == 'unknown'
        assert client.post(route, json=body).json() == first.json()
        assert len(calls) == 1
        stored = app.state.trading.engine.get_request(1, 1, body['request_id'])
        assert stored['response']['status'] == 'unknown'
        assert not list(app.state.trading.root.glob('1/production/*.json'))


def test_account_commands_serialize_and_cancellation_has_separate_identity(trading):
    app, calls = trading
    with TestClient(app) as client:
        signin(client)
        create(client)
        body = payload()
        result = client.post('/api/trading/accounts/1/orders', json=body).json()
        cancel = {'account_revision': 'f' * 64, 'request_id': str(uuid4()), 'symbol': body['symbol'],
                  'environment': 'production', 'client_order_id': result['client_order_id']}
        route = '/api/trading/accounts/1/cancel'
        first = client.post(route, json=cancel)
        assert first.json()['status'] == 'completed'
        assert client.post(route, json=cancel).json() == first.json()
        assert [c[2]['action'] for c in calls] == ['submit', 'cancel']
        assert calls[0][2]['client_order_id'] == result['client_order_id']


def test_cross_process_lock_prevents_second_dispatch(trading):
    import fcntl

    app, calls = trading
    with TestClient(app) as client:
        signin(client)
        create(client)
        directory = app.state.trading.root / '1' / '1' / 'production'
        directory.mkdir(parents=True)
        with (directory / 'account.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert client.post('/api/trading/accounts/1/orders', json=payload()).status_code == 409
        assert not calls


def test_lookup_resolves_unknown_from_venue_without_resubmitting(trading, monkeypatch):
    app, calls = trading
    async def fail(account, symbol, command):
        calls.append(command)
        raise QueryError('timeout')
    monkeypatch.setattr(manual, 'execute', fail)
    with TestClient(app) as client:
        signin(client)
        create(client)
        body = payload()
        pending = client.post('/api/trading/accounts/1/orders', json=body).json()
        async def snapshot(account, symbol, command):
            assert command == {'action': 'snapshot'}
            return {'orders': [{'client_order_id': pending['client_order_id'], 'status': 'accepted',
                               'open': True, 'symbol': symbol, 'timestamp': 1000,
                               'order_id': '123', 'side': 'buy', 'order_type': 'limit',
                               'quantity': '0.01', 'filled': '0', 'price': '2000',
                               'trigger_price': None, 'reduce_only': False}], 'records': []}
        monkeypatch.setattr(manual, 'execute', snapshot)
        result = client.get(f"/api/trading/accounts/1/requests/{body['request_id']}?environment=production")
        assert result.json()['status'] == 'completed'
        assert app.state.trading.engine.get_request(1, 1, body['request_id'])['response']['status'] == 'completed'
        assert len(calls) == 1


def test_ledger_failure_keeps_venue_result_and_replays_only_ingestion(trading, monkeypatch):
    from api.db.errors import StorageUnavailable

    app, calls = trading
    native = manual.execute
    async def execute(*args):
        result = await native(*args)
        result['records'] = [{'event_id': 'fixture'}]
        return result
    monkeypatch.setattr(manual, 'execute', execute)
    def unavailable(_):
        raise StorageUnavailable('fixture')
    monkeypatch.setattr(app.state.operations.repository, 'ingest', unavailable)
    with TestClient(app) as client:
        signin(client)
        create(client)
        body = payload()
        route = '/api/trading/accounts/1/orders'
        first = client.post(route, json=body)
        assert first.json()['status'] == 'completed'
        assert '账本暂未同步' in first.json()['message']
        ingested = []
        monkeypatch.setattr(app.state.operations.repository, 'ingest', ingested.append)
        assert client.post(route, json=body).json()['status'] == 'completed'
        assert ingested == [{'event_id': 'fixture'}]
        assert app.state.trading.engine.get_request(1, 1, body['request_id'])['ledger_synced'] is True
        assert len(calls) == 1


def test_changed_account_revision_rejects_trade_and_unknown_lookup(trading, monkeypatch):
    app, calls = trading
    async def fail(account, symbol, command):
        calls.append(command)
        raise QueryError('timeout')
    monkeypatch.setattr(manual, 'execute', fail)
    with TestClient(app) as client:
        signin(client)
        create(client)
        body = payload()
        route = '/api/trading/accounts/1/orders'
        assert client.post(route, json=payload(account_revision='e'*64)).status_code == 409
        assert not calls
        assert client.post(route, json=body).json()['status'] == 'unknown'
        monkeypatch.setattr('api.exchanges.trading.account_revision', lambda account: 'e'*64)
        result = client.get(f"/api/trading/accounts/1/requests/{body['request_id']}?environment=production")
        assert result.status_code == 409
        assert len(calls) == 1


def test_state_has_bound_account_revision_and_omits_internal_execution_evidence(trading):
    app, calls = trading
    with TestClient(app) as client:
        signin(client)
        create(client)
        response = client.get('/api/trading/accounts/1/state?symbol=ETHUSDT-PERP')
        assert response.status_code == 200
        assert response.json()['account_revision'] == 'f'*64
        assert response.json()['instrument']['size_increment'] == '0.001'
        assert 'records' not in response.json() and 'result' not in response.json()
        assert calls[0][2] == {'action': 'snapshot'}


def test_snapshot_exposes_current_symbol_leverage(trading):
    app, calls = trading
    with TestClient(app) as client:
        signin(client)
        create(client)
        result = client.get('/api/trading/accounts/1/state?symbol=ETHUSDT-PERP')
        assert result.status_code == 200
        assert result.json()['leverage'] == 20
        assert calls == [('production', 'ETHUSDT-PERP', {'action': 'snapshot'})]
