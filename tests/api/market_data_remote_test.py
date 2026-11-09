"""Business API delegates market-data reads to the tenant Engine, never host paths."""
from datetime import UTC, datetime
from types import SimpleNamespace

from api.market_data import router as data_router
from api.market_data.contracts import DataRange


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
        assert "path" not in body and "project" not in body
