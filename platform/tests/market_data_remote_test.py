"""Business API delegates market-data reads to the tenant Engine, never host paths."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from api.market_data import router as data_router
from api.market_data.contracts import DataRange
from fastapi import HTTPException


class RemoteEngine:
    remote = True

    def __init__(self):
        self.calls = []

    def data_query(self, operation, body):
        self.calls.append((operation, body))
        return {"operation": operation}

    def market_control(self, operation, body=None):
        self.calls.append((operation, body))
        return {"operation": operation}


def test_business_data_reads_use_the_engine_http_contract():
    engine = RemoteEngine()
    policy = SimpleNamespace(symbol="ETHUSDT", product="futures_um", base_level="tick")
    subscriptions = SimpleNamespace(_read=lambda: [policy])
    manager = SimpleNamespace(engine=engine, subscriptions=subscriptions)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(market_data=manager)))

    query = DataRange(
        symbol="ETHUSDT", start=datetime(2026, 8, 1, 1, tzinfo=UTC),
        end=datetime(2026, 8, 1, 1, 5, tzinfo=UTC), intervals=[],
    )
    assert data_router.datasets(request, None)["operation"] == "datasets"
    assert data_router.latest_data_times(request, None)["operation"] == "latest"
    assert data_router.data_coverage(request, None, query)["operation"] == "coverage"
    assert data_router.preview_data(request, query, 100, None)["operation"] == "page"
    assert data_router.local_chart(request, None, symbol="ETHUSDT", interval="1m")["operation"] == "chart"
    assert data_router.feed_status(request, None)["operation"] == "feed-status"

    assert [name for name, _ in engine.calls] == [
        "datasets", "latest", "coverage", "page", "chart", "feed-status",
    ]
    assert engine.calls[0][1]["symbols"] == ["ETHUSDT"]
    assert engine.calls[3][1]["journal"] is True
    assert engine.calls[3][1]["limit"] == 100
    assert engine.calls[4][1] == {"symbol": "ETHUSDT", "interval": "1m", "base_level": "tick"}
    for _, body in engine.calls:
        if body is not None:
            assert "path" not in body and "project" not in body


def test_market_export_is_streamed_from_engine_not_platform_files(tmp_path):
    from fastapi.responses import StreamingResponse

    requested = []

    class Engine:
        def market_export(self, identity):
            requested.append(identity)
            return iter([b"timestamp,price\\n", b"1,2000\\n"])

    class Manager:
        def job(self, identity):
            return {"id": identity, "action": "export", "status": "succeeded"}

        def _required_engine(self):
            return Engine()

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(market_data=Manager())),
    )
    from api.market_data import router as data_router

    identity = "a" * 32
    response = data_router.download(request, identity, None)
    assert isinstance(response, StreamingResponse)
    assert response.headers["content-disposition"] == f'attachment; filename="ticks-{identity}.csv"'
    assert requested == [identity]
    assert not (tmp_path / "data/exports").exists()


def test_data_queries_refuse_missing_engine_and_never_read_platform_files(tmp_path):
    from api.market_data.service import DataService

    manager = object.__new__(DataService)
    manager.project = tmp_path
    manager.subscriptions = SimpleNamespace(_read=list)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(market_data=manager)))
    query = DataRange(
        symbol="ETHUSDT", start=datetime(2026, 8, 1, 1, tzinfo=UTC),
        end=datetime(2026, 8, 1, 1, 5, tzinfo=UTC), intervals=[],
    )
    for operation in (
        lambda: data_router.datasets(request, None),
        lambda: data_router.latest_data_times(request, None),
        lambda: data_router.data_coverage(request, None, query),
        lambda: data_router.preview_data(request, query, 100, None),
        lambda: data_router.local_chart(request, None, symbol="ETHUSDT", interval="1m"),
        lambda: manager.storage(),
        lambda: manager.plan(query),
    ):
        with pytest.raises(HTTPException) as error:
            operation()
        assert error.value.status_code == 503
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / ".local").exists()
