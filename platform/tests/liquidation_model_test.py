"""Verified optional LIQ provider: no synthetic market-level liquidation signals."""
import pytest
from api.exchanges.liquidation_model import validate_model


def good():
    return {"source": "operator-configured-provider", "symbol": "ETHUSDT-PERP",
            "time": 1_000_000, "levels": [
                {"price": 1800.0, "notional": 500000.0, "side": "long"},
                {"price": 2200.0, "notional": 750000.0, "side": "short"},
            ]}


def test_external_liq_origin_and_freshness_are_required():
    assert validate_model(good(), "ETHUSDT-PERP", now=1_010_000)["levels"][0]["price"] == 1800.0
    for changes in [
        {"source": ""}, {"symbol": "BTCUSDT-PERP"}, {"time": 1}, {"time": 1_010_001 + 5000},
        {"levels": []}, {"levels": [{"price": 1800.0, "notional": 0.0, "side": "long"}]},
        {"levels": [{"price": 1800.0, "notional": 5.0, "side": "unknown"}]},
        {"levels": [{"price": float("nan"), "notional": 5.0, "side": "long"}]},
    ]:
        with pytest.raises(ValueError):
            validate_model({**good(), **changes}, "ETHUSDT-PERP", now=1_010_000)


def test_unconfigured_provider_never_invents_levels(monkeypatch):
    from api.exchanges.liquidation_model import configured_provider
    monkeypatch.delenv("SEEALPHA_LIQ_MODEL_ENDPOINT", raising=False)
    assert configured_provider() is None
