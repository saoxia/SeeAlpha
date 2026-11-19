"""Pure Bollinger decisions with two purchased option wings and one perpetual.

The planner does NOT place orders. A basket executor must fill protective wings
before increasing directional exposure, and reconcile partial fills.
"""
from collections import deque
from decimal import Decimal, InvalidOperation


class EvidenceError(ValueError):
    """Invalid or outdated bar, option quote or contract evidence."""


def number(value, label, *, positive=False):
    if isinstance(value, bool):
        raise EvidenceError(f"{label}无效")
    try:
        amount = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        raise EvidenceError(f"{label}无效") from None
    if not amount.is_finite() or (positive and amount <= 0):
        raise EvidenceError(f"{label}无效")
    return amount


def option_evidence(row, now, params, *, expected_symbol, side, underlying):
    if not isinstance(row, dict) or row.get("symbol") != expected_symbol:
        raise EvidenceError(f"{side}合约不匹配")
    if not expected_symbol.startswith(underlying + "-") or not expected_symbol.endswith("-" + side):
        raise EvidenceError(f"{side}期权方向或标的不匹配")
    ts, expiry = row.get("ts_ns"), row.get("expiry_ns")
    if (type(ts) is not int or not 0 <= now - ts <= params["max_age_seconds"] * 1_000_000_000):
        raise EvidenceError(f"{side}期权报价已过期")
    if type(expiry) is not int or expiry - now < params["min_dte_seconds"] * 1_000_000_000:
        raise EvidenceError(f"{side}期权即将到期")
    return {
        "ask": number(row.get("ask"), "期权买价", positive=True),
        "strike": number(row.get("strike"), "行权价", positive=True),
        "multiplier": number(row.get("multiplier"), "合约乘数", positive=True),
        "ask_size": number(row.get("ask_size"), "卖盘数量"),
        "expiry_ns": expiry,
    }


class BollingerModel:
    """State changes only after the basket was successfully accepted."""

    def __init__(self, period, deviation):
        self.closes = deque(maxlen=period)
        self.period = period
        self.deviation = number(deviation, "布林倍数", positive=True)
        self.position = 0
        self.last_ns = None

    def propose(self, snapshot, params):
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("bar"), dict):
            raise EvidenceError("缺少已收盘 K 线")
        now, bar = snapshot.get("now_ns"), snapshot["bar"]
        ts = bar.get("ts_ns")
        if type(now) is not int or now <= 0 or type(ts) is not int:
            raise EvidenceError("K 线时间无效")
        if bar.get("closed") is not True or not 0 <= now - ts <= params["max_age_seconds"] * 1_000_000_000:
            raise EvidenceError("K 线尚未收盘或过期")
        if self.last_ns is not None and ts <= self.last_ns:
            raise EvidenceError("重复或倒序的 K 线")
        close = number(bar.get("close"), "收盘价", positive=True)
        self.last_ns = ts
        self.closes.append(close)
        if len(self.closes) < self.period:
            return {"action": "warming_up", "targets": [], "new_position": self.position}
        mean = sum(self.closes, Decimal(0)) / self.period
        variance = sum(((value - mean) ** 2 for value in self.closes), Decimal(0)) / self.period
        deviation = variance.sqrt()
        lower, upper = mean - self.deviation * deviation, mean + self.deviation * deviation
        proposed = self.position
        if (self.position > 0 and close >= mean) or (self.position < 0 and close <= mean):
            proposed = 0
        elif self.position == 0 and deviation > 0:
            if close < lower:
                proposed = 1
            elif close > upper:
                proposed = -1
        if self.position == 0 and proposed == 0:
            return {"action": "flat", "targets": [], "new_position": 0}
        if proposed == 0:
            return self._flat(params, mean, lower, upper)
        if not params["put_symbol"] or not params["call_symbol"]:
            raise EvidenceError("必须配置有效期权看跌与看涨合约")
        underlying = params["symbol"].removesuffix("USDT")
        put = option_evidence(
            snapshot.get("put"), now, params,
            expected_symbol=params["put_symbol"], side="P", underlying=underlying,
        )
        call = option_evidence(
            snapshot.get("call"), now, params,
            expected_symbol=params["call_symbol"], side="C", underlying=underlying,
        )
        if put["expiry_ns"] != call["expiry_ns"] or not put["strike"] < close < call["strike"]:
            return self._flat(params, mean, lower, upper)
        contracts = number(params["option_contracts"], "期权数量", positive=True)
        quantity = min(number(params["allocation"], "目标数量", positive=True),
                       number(params["risk_limit"], "风险额度", positive=True))
        if (put["ask_size"] < contracts or call["ask_size"] < contracts
                or put["multiplier"] * contracts < quantity
                or call["multiplier"] * contracts < quantity):
            raise EvidenceError("期权卖盘深度不足或保护基础资产数量不足")
        premium = contracts * (put["ask"] * put["multiplier"] + call["ask"] * call["multiplier"])
        if premium > number(params["max_total_premium"], "最大期权费", positive=True):
            raise EvidenceError("期权总权利金预算不足")
        return {
            "action": "enter_long" if proposed > 0 else "enter_short",
            "new_position": proposed,
            "bands": tuple(format(x, "f") for x in (lower, mean, upper)),
            "premium_estimate": format(premium, "f"),
            "targets": [
                {"product": "options", "symbol": params["put_symbol"],
                 "quantity": format(contracts, "f")},
                {"product": "options", "symbol": params["call_symbol"],
                 "quantity": format(contracts, "f")},
                {"product": "futures_um", "symbol": params["symbol"],
                 "quantity": format(quantity * proposed, "f")},
            ],
        }

    def _flat(self, params, mean, lower, upper):
        return {
            "action": "exit",
            "new_position": 0,
            "bands": tuple(format(x, "f") for x in (lower, mean, upper)),
            "targets": [
                {"product": "options", "symbol": params["put_symbol"], "quantity": "0"},
                {"product": "options", "symbol": params["call_symbol"], "quantity": "0"},
                {"product": "futures_um", "symbol": params["symbol"], "quantity": "0"},
            ],
        }

    def commit(self, plan):
        if plan["action"] in {"enter_long", "enter_short", "exit"}:
            self.position = plan["new_position"]
