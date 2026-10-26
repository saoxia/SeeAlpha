import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from strategy_python.portfolio import (
    DEFAULT_CONFIG,
    create_strategies,
    load_portfolio,
    parse_portfolio,
)


def default_document():
    return json.loads(DEFAULT_CONFIG.read_text())


def test_three_members_include_two_independent_instances_of_one_strategy():
    value = default_document()
    value["strategies"][0]["risk_weight"] = "0.4"
    value["strategies"][1]["risk_weight"] = "0.3"
    third = deepcopy(value["strategies"][0])
    third.update(id="SLOW", risk_weight="0.3", parameters={"bollinger_period": 30})
    value["strategies"].append(third)
    strategies, budget = create_strategies(datetime(2026, 8, 1, tzinfo=UTC), parse_portfolio(value))
    assert len({str(strategy.strategy_id) for strategy in strategies}) == 3
    assert strategies[0].trade_state is not strategies[2].trade_state
    assert strategies[0].config.bollinger_period == 20
    assert strategies[2].config.bollinger_period == 30
    assert budget.available("LONG", Decimal(500)) == 60
    assert budget.available("SLOW", Decimal(500)) == 45


def test_disabled_member_is_not_instantiated_or_allocated_budget():
    value = default_document()
    value["strategies"][0]["risk_weight"] = "1"
    value["strategies"][1]["enabled"] = False
    strategies, budget = create_strategies(datetime(2026, 8, 1, tzinfo=UTC), parse_portfolio(value))
    assert len(strategies) == 1
    assert budget.weights == {"LONG": Decimal(1)}


def test_registered_trend_can_share_account_without_claiming_shared_risk_control():
    value = default_document()
    value["risk"] = {"mode": "none"}
    for member in value["strategies"]:
        member.pop("risk_weight")
    value["strategies"].append(
        {"id": "TREND", "strategy": "trend_following_long_5min", "parameters": {}}
    )
    strategies, budget = create_strategies(datetime(2026, 8, 1, tzinfo=UTC), parse_portfolio(value))
    assert budget is None
    assert len(strategies) == 3
    assert strategies[-1].leg.direction == 1
    value["risk"] = {"mode": "shared", "total_fraction": ".3"}
    for member, weight in zip(value["strategies"], [".4", ".3", ".3"]):
        member["risk_weight"] = weight
    with pytest.raises(ValueError, match="尚未接入"):
        create_strategies(datetime(2026, 8, 1, tzinfo=UTC), parse_portfolio(value))


@pytest.mark.parametrize(
    "case",
    [
        "unknown_field",
        "duplicate_id",
        "unknown_strategy",
        "weight_sum",
        "weight_nan",
        "disabled_all",
        "reserved",
        "name_path",
        "wrong_symbol",
        "wrong_interval",
        "bool_enabled",
        "negative_balance",
        "none_with_weight",
    ],
)
def test_invalid_manifest_is_rejected(case):
    value = default_document()
    if case == "unknown_field":
        value["risk"]["weigth"] = 1
    elif case == "duplicate_id":
        value["strategies"][1]["id"] = "LONG"
    elif case == "unknown_strategy":
        value["strategies"][0]["strategy"] = "missing"
    elif case == "weight_sum":
        value["strategies"][0]["risk_weight"] = ".4"
    elif case == "weight_nan":
        value["strategies"][0]["risk_weight"] = "NaN"
    elif case == "disabled_all":
        for member in value["strategies"]:
            member["enabled"] = False
    elif case == "reserved":
        value["strategies"][0]["parameters"]["order_id_tag"] = "SHORT"
    elif case == "name_path":
        value["name"] = "../escape"
    elif case == "wrong_symbol":
        value["market"]["data_symbol"] = "BTCUSDT"
    elif case == "wrong_interval":
        value["market"]["interval"] = "5m"
    elif case == "bool_enabled":
        value["strategies"][0]["enabled"] = "false"
    elif case == "negative_balance":
        value["account"]["starting_balance"] = "-1"
    elif case == "none_with_weight":
        value["risk"] = {"mode": "none"}
    with pytest.raises(ValueError):
        parse_portfolio(value)


@pytest.mark.parametrize(
    "parameters", [{"bollinger_period": 0}, {"bollinger_period": "20"}, {"missing": 1}]
)
def test_typed_strategy_parameters_are_validated(parameters):
    value = default_document()
    value["strategies"][0]["parameters"] = parameters
    with pytest.raises(ValueError, match="LONG 配置错误"):
        create_strategies(datetime(2026, 8, 1, tzinfo=UTC), parse_portfolio(value))


def test_duplicate_json_key_is_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"name": "a", "name": "b"}')
    with pytest.raises(ValueError, match="重复"):
        load_portfolio(path)
