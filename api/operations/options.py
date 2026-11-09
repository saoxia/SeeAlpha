"""One initial options position baseline, replayed from durable venue evidence."""

import asyncio
import hashlib
import json
import time
from decimal import Decimal, localcontext
from itertools import groupby

from core.engine_contracts import QueryError
from core.market.instruments import normalize_symbol
from core.option_records import normalize_option_record
from engine.connectors.binance import options
from engine.connectors.binance.options_account import BinanceOptionsAccountClient

DAY_MS = 86400000


def account_identity(account):
    return hashlib.sha256(json.dumps([
        account["environment"], account["credentials_encrypted"],
    ]).encode()).hexdigest()


def replay_positions(baseline, fills, exercises, through_ms):
    """Order quantities never move holdings; only real fills and exercise evidence do."""
    with localcontext() as context:
        context.prec = 220
        holdings = {row["symbol"]: {"quantity": Decimal(row["quantity"]),
                                   "entry": Decimal(row["entryPrice"])} for row in baseline["rows"]}
        events = [(row["timestamp"], 0, int(row["trade_id"]), row) for row in fills
                  if baseline["at_ms"] < row["timestamp"] <= through_ms]
        events.extend((row["createDate"], 1, int(row["id"]), row) for row in exercises
                      if baseline["at_ms"] < row["createDate"] <= through_ms)
        ordered = sorted(events, key=lambda event: event[:3])
        ambiguous = set()
        for stamp, group in groupby(ordered, key=lambda event: event[0]):
            directions = {}
            for _, kind, _, row in group:
                if kind == 0:
                    directions.setdefault(row["symbol"], set()).add(row["side"])
            ambiguous.update((stamp, symbol) for symbol, sides in directions.items() if len(sides) > 1)
        for stamp, kind, _, row in ordered:
            symbol = normalize_symbol(row["symbol"], "options")
            holding = holdings.setdefault(symbol, {"quantity": Decimal(0), "entry": None})
            old, entry = holding["quantity"], holding["entry"]
            if kind == 1:
                quantity = Decimal(row["quantity"])
                if quantity and (old == 0 or (old > 0) != (row["positionSide"] == "LONG") or quantity > abs(old)):
                    raise ValueError("行权数量与推算持仓冲突，期权操作可能缺失")
                new = old - quantity if old > 0 else old + quantity
                holding.update(quantity=new, entry=entry if new else None)
                continue
            change = Decimal(row["amount"]) * (1 if row["side"] == "buy" else -1)
            price, new = Decimal(row["price"]), old + change
            if not new:
                entry = None
            elif not old or old * new < 0:
                entry = price
            elif old * change > 0:
                entry = (abs(old) * entry + abs(change) * price) / abs(new) if entry is not None else None
            if new and (stamp, symbol) in ambiguous:
                entry = None
            holding.update(quantity=new, entry=entry)
        return [{"id": f"options:{symbol}", "symbol": symbol, "side": "long" if row["quantity"] > 0 else "short",
                 "contracts": format(abs(row["quantity"]), "f"),
                 "entry_price": format(row["entry"], "f") if row["entry"] is not None else None}
                for symbol, row in sorted(holdings.items()) if row["quantity"]]


def ledger_fills(repository, account, start_ms, end_ms):
    result, cursor = [], None
    while True:
        page = repository.list(account_id=account["id"], environment=account["environment"],
                               event_type="fill", start_ms=start_ms, end_ms=end_ms, cursor=cursor, limit=500)
        for row in page["records"]:
            try:
                normalize_symbol(row["symbol"], "options")
            except ValueError:
                continue
            if row["confirmed"]:
                result.append(row)
        if not page["has_more"]:
            return result
        last = page["records"][-1]
        cursor = (last["timestamp"], last["id"])


async def sync_options(repository, account, state, start_ms, end_ms, *, cashflows_only=False, engine=None):
    """Advance replay only after every discovered symbol's evidence has persisted."""
    if account["environment"] != "production":
        return 0, 0
    identity = account_identity(account)
    saved = state.get("options", {})
    if saved.get("identity") is not None and saved["identity"] != identity:
        # Never reinterpret an old baseline using a different private account.
        raise QueryError("期权账户凭据已变化，原持仓基准保留，需确认账户身份后重新建立基准。")
    current = {**saved, "identity": identity, "records_scanned": 0, "records_added": 0}
    if not cashflows_only:
        current.update(status="syncing", message="期权操作同步中。")
    state["options"] = current
    await asyncio.to_thread(repository.save_sync, **state)
    if engine is not None:
        client = engine.options_client(account)
    else:
        # Old isolated in-process fixtures; live SeeAlpha uses the Engine HTTP client.
        credentials = account["credentials"]
        client = BinanceOptionsAccountClient(
            credentials["apiKey"], credentials["secret"], environment=account["environment"],
        )
    baseline = current.get("baseline")
    # Initial REST acquisition is bracketed in exchange time. Activity during it
    # prevents accepting an ambiguous cutoff and is retried on the next sync.
    if baseline is None and not cashflows_only:
        before = (await engine.options_server_time(account) if engine is not None
                  else await asyncio.to_thread(options.get, "time"))["serverTime"]
        initial = await client.positions()
        after = (await engine.options_server_time(account) if engine is not None
                 else await asyncio.to_thread(options.get, "time"))["serverTime"]
        if type(before) is not int or type(after) is not int or not 0 < before <= after:
            raise QueryError("期权初始持仓时间无效。")
        if len({row["symbol"] for row in initial}) != len(initial):
            raise QueryError("期权初始持仓合约重复，拒绝覆盖持仓腿。")
        for row in initial:
            if (row.get("quoteAsset") != "USDT" or type(row.get("expiryDate")) is not int
                    or row["expiryDate"] <= 0 or type(row.get("time")) is not int
                    or not 0 < row["time"] <= after or Decimal(row["entryPrice"]) < 0):
                raise QueryError("期权初始持仓缺少有效币种、到期时间或持仓时间。")
        initial = [{key: row[key] for key in ("symbol", "quantity", "entryPrice", "expiryDate", "time")}
                   for row in initial]
    else:
        before = after = baseline["at_ms"] if baseline else end_ms
        initial = baseline["rows"] if baseline else []
    floor = int(time.time() * 1000) - 87 * DAY_MS
    next_ms = current.get("cashflows_next_ms" if cashflows_only else "next_ms", before)
    if cashflows_only:
        next_ms = max(next_ms, floor)
    if next_ms < floor:
        raise QueryError("期权操作缺口超出历史保留期，保留旧持仓，不能自动推算最新持仓。")
    first = min(start_ms, next_ms - DAY_MS) if baseline or cashflows_only else min(start_ms, before)
    first, last = max(floor, first), end_ms if cashflows_only else max(end_ms, after)
    symbols = set(current.get("symbols", [])) | {row["symbol"] for row in initial}
    open_page = {"orders": [], "possibly_truncated": False} if cashflows_only else await client.open_orders()
    if open_page["possibly_truncated"]:
        raise QueryError("期权挂单满页，无法确认合约发现范围。")
    symbols.update(row["symbol"] for row in open_page["orders"])
    scanned = added = 0
    warnings = []
    exercise_rows = {str(row["id"]): row for row in current.get("exercises", [])}
    async for page in client.history_pages("bill", first, last):
        for row in page["rows"]:
            if row.get("symbol"):
                symbols.add(row["symbol"])
            elif not cashflows_only and row["type"] not in {"TRANSFER", "TRANSFER_IN", "TRANSFER_OUT"}:
                raise QueryError("期权合约资金记录缺少合约，不能确认持仓推算覆盖。")
            result = await asyncio.to_thread(repository.ingest, normalize_option_record(row, account, "cashflow"))
            scanned += 1
            added += int(result["created"])
            current.update(records_scanned=scanned, records_added=added)
    if cashflows_only:
        current.update(symbols=sorted(symbols), cashflows_next_ms=max(next_ms, last + 1), cashflows_status="success")
        current.setdefault("message", "期权资金流水已同步，持仓基准等待完整操作同步建立。")
        await asyncio.to_thread(repository.save_sync, **state)
        return scanned, added
    async for page in client.history_pages("exerciseRecord", first, last):
        for row in page["rows"]:
            row = {key: row[key] for key in ("id", "symbol", "positionSide", "quantity", "createDate",
                                            "amount", "fee", "exercisePrice", "quoteAsset")}
            row["id"] = str(row["id"])
            symbols.add(row["symbol"])
            previous = exercise_rows.get(row["id"])
            if previous is not None and previous != row:
                raise QueryError("期权行权身份内容冲突，保留原证据。")
            exercise_rows[row["id"]] = row
        current.update(symbols=sorted(symbols), exercises=list(exercise_rows.values()))
        await asyncio.to_thread(repository.save_sync, **state)
    boundary_activity = any(before <= row["createDate"] <= after for row in exercise_rows.values()) if not baseline else False
    for symbol in sorted(symbols):
        async for page in client.history_pages("userTrades", first, last, symbol=symbol):
            for row in page["rows"]:
                if not baseline and before <= row["time"] <= after:
                    boundary_activity = True
                result = await asyncio.to_thread(repository.ingest, normalize_option_record(row, account, "fill"))
                scanned += 1
                added += int(result["created"])
                current.update(records_scanned=scanned, records_added=added)
        # Completed orders have a shorter retention period than fills. Missing
        # order lifecycle observations do not turn cumulative executedQty into fills.
        history_start = max(first, int(time.time() * 1000) - 5 * DAY_MS + 1000)
        if history_start <= last:
            page = await client.order_history(symbol, history_start, last)
            for row in page["orders"]:
                result = await asyncio.to_thread(repository.ingest, normalize_option_record(row, account, "order"))
                scanned += 1
                added += int(result["created"])
                current.update(records_scanned=scanned, records_added=added)
            if page["possibly_truncated"]:
                warnings.append("部分期权订单历史满页，订单生命周期可能不完整。")
    for row in open_page["orders"]:
        result = await asyncio.to_thread(repository.ingest, normalize_option_record(row, account, "order"))
        scanned += 1
        added += int(result["created"])
        current.update(records_scanned=scanned, records_added=added)
    if boundary_activity:
        raise QueryError("期权初始快照期间有成交或行权，已保存操作，等待稳定边界后重试建立基准。")
    if baseline is None:
        # Keep the entire acquisition millisecond in the replay range. A fill
        # arriving later with the same timestamp as the end bracket is not lost.
        baseline = {"at_ms": before - 1, "captured_at_ms": after, "rows": initial}
    through = max(current.get("through_ms", baseline["at_ms"]), last)
    fills = await asyncio.to_thread(ledger_fills, repository, account, baseline["at_ms"] + 1, through)
    positions = replay_positions(baseline, fills, exercise_rows.values(), through)
    current.update(baseline=baseline, symbols=sorted(symbols), exercises=list(exercise_rows.values()),
                   positions=positions, next_ms=max(next_ms, last + 1), through_ms=through,
                   status="partial", last_success_at=int(time.time() * 1000),
                   message="期权持仓由初始基准、真实成交与行权推算；合约发现及交易所历史保留范围有限。" + "".join(set(warnings)))
    expired = {row["symbol"] for row in baseline["rows"] if row["expiryDate"] <= through}
    if any(row["symbol"] in expired for row in positions):
        current["message"] += " 部分持仓已到期但尚无足够行权证据，保留推算数量待核实。"
    await asyncio.to_thread(repository.save_sync, **state)
    return scanned, added


def position_view(repository, account):
    if account["environment"] != "production":
        return [], "期权仅支持正式环境，测试账户未读取期权。"
    state = next((row.get("options", {}) for row in repository.sync_state(account["id"])
                  if row["environment"] == account["environment"]), {})
    if not state.get("baseline"):
        return [], state.get("message", "期权持仓尚未建立初始基准，等待操作同步。")
    if state.get("identity") != account_identity(account):
        return [], "期权账户凭据已变化，原持仓基准未用于新凭据。"
    warning = state["message"] + " 未接入当前标记价，期权市值与浮盈亏显示为 --；期权资产未并入 USD-M 账户权益。"
    through = state.get("through_ms", state["baseline"]["at_ms"])
    if state.get("status") == "failed" or int(time.time() * 1000) - through > 600000:
        warning += " 期权操作同步失败或已过期，以下为上次推算结果。"
    return [{**row, "account_id": account["id"], "account_name": account["account_name"], "exchange": account["exchange"]}
            for row in state.get("positions", [])], warning
