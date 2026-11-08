"""Deterministic Decimal valuation of complete evidence, never a trading input."""

from decimal import Decimal, InvalidOperation, localcontext

VERSION = 1


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("资产证据包含非有限金额。")
    return result


def value(evidence):
    """No missing prices as zero; stale or disconnected evidence creates a gap."""
    result = {"version": VERSION, "total_equity": None, "wallet_balance": None,
              "unrealized_pnl": None, "reason": evidence["reason"], "gap": True,
              "error": None}
    try:
        if evidence["reason"] == "gap":
            raise ValueError("账户事件流中断，需要新的对账基准。")
        state, now = evidence["state"], evidence["time"]
        if not state["wallet"] or not -5000 <= now - state.get("account_time", now) <= 15_000:
            raise ValueError("缺少完整钱包基准或账户状态已过期。")
        with localcontext() as context:
            context.prec = 50

            def rate(currency):
                if currency == "USDT":
                    return Decimal(1)
                item = state["fx"][currency]
                if not -5000 <= now - item["time"] <= 120_000 or decimal(item["price"]) <= 0:
                    raise ValueError("资产折算价格缺失或已过期。")
                return decimal(item["price"])

            wallet = sum((decimal(amount) * rate(currency)
                          for currency, amount in state["wallet"].items() if decimal(amount) != 0), Decimal(0))
            pnl = Decimal(0)
            identities = set()
            for position in state["positions"]:
                if position["id"] in identities:
                    raise ValueError("仓位身份重复。")
                identities.add(position["id"])
                quantity, entry = decimal(position["quantity"]), decimal(position["entry"])
                if quantity == 0 or entry <= 0:
                    raise ValueError("仓位数量或开仓价无效。")
                mark = state["marks"][position["instrument"]]
                price = decimal(mark["price"])
                if price <= 0 or not -5000 <= now - mark["time"] <= 30_000:
                    raise ValueError("持仓标记价格缺失或已过期。")
                pnl += quantity * (price - entry) * rate(position["currency"])
            result.update(total_equity=format(wallet + pnl, "f"), wallet_balance=format(wallet, "f"),
                          unrealized_pnl=format(pnl, "f"), gap=False)
    except (KeyError, ValueError, InvalidOperation, TypeError):
        result["error"] = "估值证据不完整、价格过期或事件流中断；本点不计入资产曲线。"
    return result


def positions_key(positions):
    # Native reconciliation may assign a different local position ID to the same leg.
    return sorted((p["instrument"], p["currency"], decimal(p["quantity"]))
                  for p in positions)


def reconcile(current, observed, previous_breach=False):
    """Compare both states using exactly the same mark/FX evidence, excluding quote drift."""
    left, right = value(current), value(observed)
    before, after = current["state"], observed["state"]
    currencies = before["wallet"].keys() | after["wallet"].keys()
    structural = any(decimal(before["wallet"].get(c, 0)) != decimal(after["wallet"].get(c, 0))
                     for c in currencies) or positions_key(before["positions"]) != positions_key(after["positions"])
    delta = None
    threshold = None
    if left["total_equity"] is not None and right["total_equity"] is not None:
        delta = decimal(right["total_equity"]) - decimal(left["total_equity"])
        threshold = max(Decimal(1), abs(decimal(right["total_equity"])) * Decimal("0.0005"))
    breach = delta is not None and abs(delta) > threshold
    return {"comparable": delta is not None, "difference": str(delta) if delta is not None else None,
            "threshold": str(threshold) if threshold is not None else None,
            "structural": structural, "breach": breach,
            "calibrate": structural or breach and previous_breach}
