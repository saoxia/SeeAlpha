"""Scoped development research: durable budgets, real resource gates, no Engine admin bearer."""
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from api.research.direct_engine import DirectEngineResearchClient
from api.research.grants import ResearchGrant
from api.research.task_access import ResearchLeases, allowed_scope
from api.tenant_docker import DockerTenantManager
from core.engine_contracts import EngineConflict
from fastapi import HTTPException


@pytest.fixture
def leases(tmp_path):
    return ResearchLeases(tmp_path / "private" / "research.sqlite3")


def _issue(store, task_id="task-uuid", turn=1):
    return store.issue(
        task_id=task_id, owner_id=7, tenant_id=11,
        account_id="5", dataset_ref="futures_um:ETHUSDT:1m", turn=turn,
    )


def test_task_token_is_hashed_persistent_and_turn_bound(leases):
    token, expires = _issue(leases)
    assert expires > 0
    raw = Path(leases.path).read_bytes()
    assert token.encode() not in raw
    assert Path(leases.path).stat().st_mode & 0o077 == 0
    assert leases.authenticated("task-uuid", token)["owner_id"] == 7
    assert leases.consume("task-uuid", token, 1, "datasets")["remaining"] == 80
    after_restart = ResearchLeases(leases.path)
    assert after_restart.authenticated("task-uuid", token)["turn"] == 1
    replacement, _ = _issue(after_restart, turn=2)
    assert replacement != token
    with pytest.raises(HTTPException) as rejected:
        after_restart.authenticated("task-uuid", token)
    assert rejected.value.status_code == 403
    with pytest.raises(HTTPException):
        after_restart.consume("task-uuid", replacement, 1, "datasets")
    assert after_restart.consume("task-uuid", replacement, 2, "datasets")["turn"] == 2
    after_restart.revoke("task-uuid")
    with pytest.raises(HTTPException):
        after_restart.authenticated("task-uuid", replacement)


def test_rejects_forged_expired_and_unbudgeted_calls(leases):
    token, _ = _issue(leases)
    for wrong in ("a" * 64, token[:-1] + "A"):
        with pytest.raises(HTTPException):
            leases.authenticated("task-uuid", wrong)
    with pytest.raises(HTTPException) as err:
        leases.consume("task-uuid", token, 1, "shell")
    assert err.value.status_code == 404
    with leases._connect() as db:
        db.execute("UPDATE research_leases SET remaining=0 WHERE task_id='task-uuid'")
    with pytest.raises(HTTPException) as err:
        leases.consume("task-uuid", token, 1, "datasets")
    assert err.value.status_code == 429
    with leases._connect() as db:
        db.execute("UPDATE research_leases SET expires_at=1 WHERE task_id='task-uuid'")
    with pytest.raises(HTTPException):
        leases.authenticated("task-uuid", token)


def test_scope_checks_active_owner_account_issuance_policy(monkeypatch):
    from api.research import task_access

    tenant = {"id": 11, "owner_id": 7, "purpose": "development", "status": "active"}
    accounts = [{"id": 5}]
    policy = [{"product": "futures_um", "symbol": "ETHUSDT",
               "parameters": {"history_enabled": True, "intervals": ["1m", "5m"]}}]
    loads = [{"account_id": "5", "purpose": "development",
              "parameters": {"symbol": "ETHUSDT"}}]
    class FakePolicy:
        def __init__(self, _database):
            pass
        def records(self, _tenant_id):
            return policy
    monkeypatch.setattr(task_access, "PolicyRepository", FakePolicy)
    app = SimpleNamespace(state=SimpleNamespace(
        database=object(),
        tenants=SimpleNamespace(repository=SimpleNamespace(
            get_tenant=lambda _id: tenant,
            list_virtual_accounts=lambda _id: accounts,
        )),
        user_resources=SimpleNamespace(enabled=lambda _id, _kind: True),
        strategies=SimpleNamespace(repository=SimpleNamespace(
            list_issuances=lambda _id: loads,
        )),
    ))
    def allowed():
        return allowed_scope(app, 7, 11, "5", "futures_um:ETHUSDT:1m")
    assert allowed() is True
    assert allowed_scope(app, 8, 11, "5", "futures_um:ETHUSDT:1m") is False
    assert allowed_scope(app, 7, 11, "99", "futures_um:ETHUSDT:1m") is False
    assert allowed_scope(app, 7, 11, "5", "futures_um:BTCUSDT:1m") is False
    tenant["purpose"] = "production"
    assert allowed() is False
    tenant["purpose"] = "development"
    policy[0]["parameters"]["history_enabled"] = False
    assert allowed() is False
    policy[0]["parameters"]["history_enabled"] = True
    loads[0]["parameters"]["symbol"] = "BTCUSDT"
    assert allowed() is False


def test_local_docker_research_key_only_on_explicit_new_development(tmp_path):
    def manager(enabled):
        return DockerTenantManager(
            tmp_path / ("enabled" if enabled else "disabled"),
            image="engine:pinned", production_cpus="1", production_memory="1g",
            development_cpus="1", development_memory="1g", log_root=tmp_path,
            research_enabled=enabled,
        )
    enabled = manager(True)
    root, *_other, path = enabled._paths("11")
    root.mkdir(parents=True, exist_ok=True)
    enabled._credentials(path, "11", "development", create=True)
    key = enabled.research_signing_key("11")
    assert len(key) >= 32
    assert key not in enabled._credentials(path, "11", "development", create=False)
    with pytest.raises(EngineConflict):
        enabled.research_signing_key("12")
    disabled = manager(False)
    other, *_other, path2 = disabled._paths("11")
    other.mkdir(parents=True, exist_ok=True)
    disabled._credentials(path2, "11", "development", create=True)
    with pytest.raises(EngineConflict):
        disabled.research_signing_key("11")


def test_direct_client_uses_scoped_grant_only_not_engine_admin_token():
    grant = ResearchGrant(
        token="test-scoped-engine-grant", tenant_id="11", task_id="task-1",
        expires_at=1900000000, fingerprint="digest",
    )
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer test-scoped-engine-grant"
        assert request.url.path.startswith("/research/v1/")
        return httpx.Response(
            200,
            headers={"X-SeeAlpha-Engine-Contract-Version": "2",
                     "X-SeeAlpha-Research-Contract-Version": "1"},
            json={"datasets": []},
        )
    with DirectEngineResearchClient(
        "https://development-engine.local", grant,
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.discover_datasets() == {"datasets": []}
    assert len(seen) == 1


def _archive_candidate(source=None):
    import base64
    import hashlib
    import io
    import json
    import zipfile

    source = source or (
        "CANDIDATE = {'kind':'ema_cross','symbol':'ETHUSDT',"
        "'fast_period':8,'slow_period':21,'allocation':'1','risk_limit':'1'}\n"
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("seealpha-package.json", json.dumps({
            "format": 1, "name": "research_ema", "title": "AI EMA",
            "version": "1", "entry_point": "strategy:CANDIDATE",
        }))
        archive.writestr("strategy.py", source)
    raw = stream.getvalue()
    return base64.b64encode(raw).decode(), hashlib.sha256(raw).hexdigest()


def test_candidate_versions_are_private_immutable_and_reusable_across_turns(leases):
    from api.research.candidates import parse_candidate

    encoded, digest = _archive_candidate()
    archive, parsed = parse_candidate(encoded)
    token, _ = _issue(leases)
    row = {**leases.consume("task-uuid", token, 1, "candidate_create"), "task_id": "task-uuid"}
    first = leases.create_candidate(row, archive, parsed)
    second = leases.create_candidate(row, archive, parsed)
    assert first["candidate_id"] != second["candidate_id"]
    assert first["package_hash"] == digest
    selected = leases.get_candidate(row, first["candidate_id"])
    assert selected["archive"] == archive
    assert selected["package_hash"] == digest
    assert selected["recipe"]["kind"] == "ema_cross"
    with pytest.raises(HTTPException):
        leases.get_candidate({**row, "task_id": "foreign"}, first["candidate_id"])
    with pytest.raises(HTTPException):
        leases.get_candidate(row, "not-a-candidate")
    leases.revoke("task-uuid")
    with pytest.raises(HTTPException):
        leases.create_candidate(row, archive, parsed)


def test_candidate_validator_never_executes_user_python():
    from api.research.candidates import parse_candidate

    archive, _ = _archive_candidate()
    _, parsed = parse_candidate(archive)
    assert parsed["recipe"]["symbol"] == "ETHUSDT"
    for source in (
        "import os\nCANDIDATE = {}",
        "CANDIDATE = __import__('os').system('id')",
        "CANDIDATE = {}\nopen('/etc/passwd').read()",
    ):
        encoded, _ = _archive_candidate(source)
        with pytest.raises(ValueError):
            parse_candidate(encoded)
    with pytest.raises(ValueError):
        parse_candidate("not-base64")
    malicious_quantity, _ = _archive_candidate(
        "CANDIDATE = {'kind':'ema_cross','symbol':'ETHUSDT',"
        "'fast_period':8,'slow_period':21,'allocation':'1','risk_limit':'1E+99999999'}"
    )
    with pytest.raises(ValueError):
        parse_candidate(malicious_quantity)


def test_candidate_engine_client_only_uses_restricted_submission():
    from api.research.direct_engine import DirectEngineResearchClient

    encoded, digest = _archive_candidate()
    scope = {"candidate_id": "a" * 32, "package_hash": digest,
             "archive": encoded, "strategy_ref": "account:5",
             "dataset_refs": ["futures_um:ETHUSDT:1m"],
             "start": "2026-01-01", "end": "2026-01-02",
             "starting_balance": "1000", "quote_currency": "USDT",
             "idempotency_key": "ai-iteration-1"}
    grant = ResearchGrant(
        token="scoped-hmac-grant", task_id="task",
        tenant_id="11", expires_at=1900000000, fingerprint="abc",
    )
    seen = []
    def responder(request):
        seen.append(request)
        assert request.url.path == "/research/v1/candidate-backtests"
        assert request.headers["authorization"] == "Bearer scoped-hmac-grant"
        payload = __import__("json").loads(request.content)
        assert payload["purpose"] == "development"
        assert payload["execution"] == "simulation"
        assert payload["package_hash"] == digest
        return httpx.Response(202, headers={
            "X-SeeAlpha-Engine-Contract-Version": "2",
            "X-SeeAlpha-Research-Contract-Version": "1",
        }, json={"backtest_id": "1234", "status": "queued"})
    with DirectEngineResearchClient(
        "https://engine.example", grant, transport=httpx.MockTransport(responder),
    ) as client:
        assert client.submit_candidate(scope)["backtest_id"] == "1234"
        with pytest.raises(ValueError):
            client.submit_candidate({**scope, "path": "/tmp/strategy.py"})
    assert len(seen) == 1


def _full_python_archive(*, symbol="ETHUSDT", source=None):
    import base64
    import hashlib
    import io
    import json
    import zipfile

    source = source if source is not None else (
        "from nautilus_trader.trading import Strategy\n"
        "class AiSignal(Strategy):\n"
        "    def on_bar(self, bar): pass\n"
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("seealpha-package.json", json.dumps({
            "format": 1, "name": "ai_signal", "title": "AI Research",
            "version": "1", "entry_point": "strategy:AiSignal",
            "parameters": {
                "symbol": symbol, "allocation": "1", "risk_limit": "1",
            },
        }))
        z.writestr("strategy.py", source)
    raw = stream.getvalue()
    return base64.b64encode(raw).decode(), hashlib.sha256(raw).hexdigest()


def test_full_python_candidate_is_immutable_and_task_scoped(leases, monkeypatch):
    from api.research import task_access
    from api.research.python_candidates import parse_python_candidate

    token, _ = _issue(leases)
    row = {**leases.consume("task-uuid", token, 1, "python_candidate_create"), "task_id": "task-uuid"}
    encoded, digest = _full_python_archive()
    data, parsed = parse_python_candidate(encoded)
    assert parsed["recipe"]["mode"] == "python"
    assert parsed["recipe"]["entry_point"] == "strategy:AiSignal"
    created = leases.create_candidate(row, data, parsed)
    assert created["package_hash"] == digest
    saved = leases.get_candidate(row, created["candidate_id"])
    assert saved["archive"] == data
    assert saved["recipe"]["parameters"]["symbol"] == "ETHUSDT"
    with pytest.raises(HTTPException):
        leases.get_candidate({**row, "tenant_id": 12}, created["candidate_id"])

    app = SimpleNamespace(state=SimpleNamespace(
        settings=SimpleNamespace(research_python_enabled=True, engine_token=""),
        research_leases=leases,
        tenants=SimpleNamespace(repository=object()),
    ))
    monkeypatch.setattr(task_access, "allowed_scope", lambda *args, **kwargs: True)
    issued = []
    class FakeGrantIssuer:
        def __init__(self, **kwargs):
            pass
        def issue(self, **kwargs):
            issued.append(kwargs)
            return ResearchGrant(
                token="test-scoped-engine-grant", tenant_id="11",
                task_id="task-uuid", expires_at=1900000000, fingerprint="x",
            )
    seen = []
    class FakeClient:
        def __init__(self, origin, grant):
            seen.append((origin, grant.token))
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def submit_python_candidate(self, request):
            seen.append(request)
            return {"backtest_id": "job-1", "status": "queued"}
    monkeypatch.setattr(task_access, "engine_connection", lambda _app, tenant: (
        f"https://engine-{tenant}.internal", "test-signing-key",
    ))
    monkeypatch.setattr(task_access, "ResearchGrantIssuer", FakeGrantIssuer)
    monkeypatch.setattr(task_access, "EngineMCPResearchClient", FakeClient)
    args = {
        "candidate_id": created["candidate_id"],
        "start": "2026-01-01", "end": "2026-01-02",
        "starting_balance": "1000", "quote_currency": "USDT",
        "idempotency_key": "iteration-1",
    }
    assert task_access.research_command(
        app, row, "python_candidate_submit", args,
    )["backtest_id"] == "job-1"
    assert seen[0][0] == "https://engine-11.internal"
    assert seen[1]["package_hash"] == digest
    assert seen[1]["strategy_ref"] == "account:5"
    assert seen[1]["dataset_refs"] == ["futures_um:ETHUSDT:1m"]
    assert issued[0]["operations"] == ("python_candidates:submit",)
    with pytest.raises(HTTPException):
        task_access.research_command(app, row, "candidate_submit", args)
    app.state.settings.research_python_enabled = False
    with pytest.raises(HTTPException):
        task_access.research_command(app, row, "python_candidate_submit", args)


def test_full_python_candidate_preflight_rejects_wrong_symbol_and_entry():
    from api.research.python_candidates import parse_python_candidate

    encoded, digest = _full_python_archive()
    _, parsed = parse_python_candidate(encoded)
    assert parsed["sha256"] == digest
    wrong_entry, _ = _full_python_archive(source="print('no class')\n")
    with pytest.raises(ValueError):
        parse_python_candidate(wrong_entry)
    with pytest.raises(ValueError):
        parse_python_candidate("not-base64")
    with pytest.raises(ValueError):
        parse_python_candidate("e" * 400_001)


def test_platform_direct_client_python_candidate_uses_only_pinned_engine_url():
    from api.research.direct_engine import DirectEngineResearchClient

    encoded, digest = _full_python_archive()
    grant = ResearchGrant(
        token="granted-research-hmac", tenant_id="11",
        task_id="task-uuid", expires_at=1900000000, fingerprint="x",
    )
    def transport(request):
        assert request.url.host == "pinned-development-engine.internal"
        assert request.url.path == "/research/v1/python-candidate-backtests"
        assert request.headers["Authorization"] == "Bearer granted-research-hmac"
        import json
        payload = json.loads(request.content)
        assert payload["purpose"] == "development"
        assert payload["execution"] == "simulation"
        assert payload["package_hash"] == digest
        return httpx.Response(202, headers={
            "X-SeeAlpha-Engine-Contract-Version": "2",
            "X-SeeAlpha-Research-Contract-Version": "1",
        }, json={"backtest_id": "job-2"})
    with DirectEngineResearchClient(
        "https://pinned-development-engine.internal",
        grant, transport=httpx.MockTransport(transport),
    ) as client:
        assert client.submit_python_candidate({
            "candidate_id": "a" * 32, "package_hash": digest, "archive": encoded,
            "strategy_ref": "account:5", "dataset_refs": ["futures_um:ETHUSDT:1m"],
            "start": "2026-01-01", "end": "2026-01-02",
            "starting_balance": "1000", "quote_currency": "USDT",
            "idempotency_key": "iteration-1",
        })["backtest_id"] == "job-2"


def test_backend_controls_python_flag_only_for_new_development_docker(tmp_path):
    def manager(root, python):
        return DockerTenantManager(
            root, image="pinned-engine:v1",
            production_cpus="1", production_memory="1g",
            development_cpus="1", development_memory="1g",
            log_root=tmp_path, research_enabled=True,
            research_python_enabled=python,
        )

    enabled = manager(tmp_path / "enabled", True)
    for tenant_id, purpose in (("11", "development"), ("12", "production")):
        root, *_dirs, env = enabled._paths(tenant_id)
        root.mkdir(parents=True, exist_ok=True)
        enabled._credentials(env, tenant_id, purpose, create=True)
        text = env.read_text()
        assert "ENGINE_AUTH_MODE=token" in text
        assert "ENGINE_TOKEN=" in text
        assert ("ENGINE_RESEARCH_PYTHON_ENABLED=true" in text) == (
            purpose == "development"
        )
        assert ("ENGINE_RESEARCH_SIGNING_KEY=" in text) == (
            purpose == "development"
        )

    old = manager(tmp_path / "existing", False)
    root, *_dirs, env = old._paths("13")
    root.mkdir(parents=True, exist_ok=True)
    old._credentials(env, "13", "development", create=True)
    assert "ENGINE_RESEARCH_PYTHON_ENABLED" not in env.read_text()
    later = manager(tmp_path / "existing", True)
    later._credentials(env, "13", "development", create=False)
    assert "ENGINE_RESEARCH_PYTHON_ENABLED" not in env.read_text()
