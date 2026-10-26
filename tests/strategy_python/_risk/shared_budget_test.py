from decimal import Decimal

import pytest

from strategy_python._risk.shared_budget import SharedRiskBudget


def test_weights_and_pending_commitments_share_one_limit():
    commitments = {"long": Decimal(0), "short": Decimal(0)}
    budget = SharedRiskBudget(Decimal("0.30"), {"long": Decimal("0.6"), "short": Decimal("0.4")})
    for name in commitments:
        budget.register(name, lambda key=name: commitments[key])
    assert budget.available("long", Decimal(500)) == 90
    assert budget.available("short", Decimal(500)) == 60
    commitments["long"] = Decimal(90)
    assert budget.available("long", Decimal(500)) == 0
    # 权益下降后，既有多头承诺同时挤占组合余量和空头自己的分配。
    assert budget.available("short", Decimal(400)) == 30
    commitments["short"] = Decimal(30)
    assert budget.available("long", Decimal(300)) == 0
    assert budget.available("short", Decimal(300)) == 0
    commitments["long"] = Decimal(0)
    assert budget.available("short", Decimal(400)) == 18
    assert budget.available("long", Decimal(400)) == 72


@pytest.mark.parametrize(
    "fraction,weight",
    [("NaN", ".5"), ("0", ".5"), ("1.1", ".5"), (".3", "0"), (".3", "1"), (".3", "Infinity")],
)
def test_invalid_budget_fails_early(fraction, weight):
    value = Decimal(weight)
    with pytest.raises(ValueError):
        SharedRiskBudget(Decimal(fraction), {"long": value, "short": 1 - value})


def test_budget_requires_complete_and_unique_members():
    budget = SharedRiskBudget(Decimal(".3"), {"long": Decimal(".5"), "short": Decimal(".5")})
    budget.register("long", lambda: Decimal(0))
    with pytest.raises(RuntimeError):
        budget.available("long", Decimal(500))
    with pytest.raises(ValueError):
        budget.register("long", lambda: Decimal(0))
