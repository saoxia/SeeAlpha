"""Deterministic, exchange-agnostic spot/perpetual funding carry planning.

Prices, rates, positions and clocks MUST come from verified independent sources.
The output is a single indivisible basket intent, never two independent orders.
"""
from decimal import Decimal, InvalidOperation


class EvidenceError(ValueError):
    """Missing, stale or inconsistent multi-market evidence."""


def finite(value, label, *, positive=False):
    if isinstance(value, bool):
        raise EvidenceError(f"{label}无效")
    try:
        result = Decimal(str(value))
    except (TypeError, InvalidOperation, ValueError):
        raise EvidenceError(f"{label}无效") from None
    if not result.is_finite() or (positive and result <= 0):
        raise EvidenceError(f"{label}无效")
    return result


def snapshot_plan(snapshot, params):
    """Plan equal BASE-asset units, spot long and USDT-M perpetual short.

    Required snapshot:
      now_ns, spot={price,ts_ns}, perpetual={price,ts_ns},
      funding={rate,ts_ns}, positions={spot,perpetual}.
    A nonpositive or subthreshold rate unwinds both legs. The funding estimate
    does not include basis changes, trading fees, borrow costs or margin PnL.
    """
    if not isinstance(snapshot, dict):
        raise EvidenceError("缺少市场快照")
    now = snapshot.get("now_ns")
    if type(now) is not int or now <= 0:
        raise EvidenceError("快照时间无效")
    prices = {}
    for product in ("spot", "perpetual"):
        row = snapshot.get(product)
        if not isinstance(row, dict):
            raise EvidenceError(f"缺少{product}行情")
        ts = row.get("ts_ns")
        if type(ts) is not int or not 0 <= now - ts <= params["max_age_seconds"] * 1_000_000_000:
            raise EvidenceError(f"{product}行情过期或来自未来")
        prices[product] = finite(row.get("price"), f"{product}价格", positive=True)
    funding = snapshot.get("funding")
    if not isinstance(funding, dict):
        raise EvidenceError("缺少资金费率")
    rate_ts = funding.get("ts_ns")
    if (type(rate_ts) is not int
            or not 0 <= now - rate_ts <= params["max_age_seconds"] * 1_000_000_000):
        raise EvidenceError("资金费率过期或来自未来")
    rate = finite(funding.get("rate"), "资金费率")
    positions = snapshot.get("positions")
    if not isinstance(positions, dict):
        raise EvidenceError("缺少两个市场的实际仓位")
    held_spot = finite(positions.get("spot"), "现货仓位")
    held_perp = finite(positions.get("perpetual"), "永续仓位")
    if held_spot < 0 or held_perp > 0:
        return {"action": "blocked", "reason": "仓位方向异常，需人工对账", "targets": []}
    drift = abs(held_spot + held_perp)
    if drift > finite(params["max_leg_drift"], "最大腿间偏差"):
        return {"action": "blocked", "reason": "跨市场净敞口超限，需先对账", "targets": []}
    basis_bps = (prices["perpetual"] / prices["spot"] - Decimal(1)) * 10_000
    permitted = (
        rate >= finite(params["min_funding_rate"], "最低费率")
        and abs(basis_bps) <= finite(params["max_basis_bps"], "最大基差")
    )
    quantity = min(
        finite(params["allocation"], "目标数量", positive=True),
        finite(params["risk_limit"], "风险上限", positive=True),
    ) if permitted else Decimal(0)
    symbol = params["symbol"]
    targets = [
        {"product": "spot", "symbol": symbol, "quantity": format(quantity, "f")},
        {"product": "futures_um", "symbol": symbol, "quantity": format(-quantity, "f")},
    ]
    return {
        "action": "carry" if permitted else "flat",
        "reason": "资金费率及基差满足条件" if permitted else "费率不足或基差过大",
        "basis_bps": format(basis_bps, "f"),
        "expected_gross_funding": format(quantity * prices["perpetual"] * rate, "f"),
        "targets": targets,
    }
