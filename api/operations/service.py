"""Project-owned ledger ingestion and bounded native reconciliation reads."""

import asyncio
import logging
import math
import time

from engine.connectors.binance.income import DAY_MS, income_history
from engine.connectors.binance.operations import operation_history
from engine.connectors.binance.queries import QueryError

from .options import sync_options
from .repository import Operations

logger = logging.getLogger(__name__)


class OperationService:
    def __init__(self, database, accounts):
        if database.backend == "orm":
            from .orm_repository import ORMOperations

            self.repository = ORMOperations(database)
        else:
            self.repository = Operations(database)
        self.accounts = accounts
        self.lock = asyncio.Lock()

    def record_project_event(self, event, order, account):
        """Called by an execution worker with its bound account, never by a browser.

        Live strategies should enqueue callbacks and persist them from their worker;
        do not run database/network I/O on the native execution callback thread.
        Backtests must not call this live-business sink.
        """
        from engine.connectors.binance.operations import normalize_event

        record = normalize_event(event, order, account)
        return self.repository.ingest(record) if record is not None else None

    async def _sync_income(self, account, start_ms, end_ms, state):
        cash = state["cashflows"]
        cash.update(status="syncing", requested_start_ms=start_ms, requested_end_ms=end_ms,
                    requested_complete=False)
        await asyncio.to_thread(self.repository.save_sync, **state)
        async for page in income_history(account, start_ms, end_ms):
            for record in page["records"]:
                result = await asyncio.to_thread(self.repository.ingest, record)
                cash["records_scanned"] += 1
                cash["records_added"] += int(result["created"])
            # Only acknowledge a whole validated, persisted leaf window, including empty ones.
            intervals = sorted([*cash.get("coverage", []), [page["start_ms"], page["end_ms"]]])
            merged = []
            for first, last in intervals:
                if merged and first <= merged[-1][1] + 1:
                    merged[-1][1] = max(last, merged[-1][1])
                else:
                    merged.append([first, last])
            cash["coverage"] = merged
            watermark = cash["next_ms"]
            for first, last in merged:
                if first <= watermark <= last + 1:
                    watermark = max(watermark, last + 1)
            cash.update(next_ms=watermark, last_success_at=int(time.time() * 1000))
            await asyncio.to_thread(self.repository.save_sync, **state)
        cash.update(status="success", requested_complete=True,
                    message="已保存请求区间的合约资金流水；接口保留期及迟到记录限制仍适用。")
        await asyncio.to_thread(self.repository.save_sync, **state)

    async def sync(self, account_id=None, start_ms=None, end_ms=None, cashflows_only=False):
        if self.lock.locked():
            from fastapi import HTTPException

            raise HTTPException(409, "已有操作记录同步正在运行。")
        async with self.lock:
            now = int(time.time() * 1000)
            explicit_start = start_ms
            end_ms = end_ms or now
            start_ms = start_ms or end_ms - 24 * 60 * 60 * 1000
            if (
                not 0 < start_ms <= end_ms <= now + 60_000
                or now - start_ms > 87 * 24 * 60 * 60 * 1000
            ):
                from fastapi import HTTPException

                raise HTTPException(
                    422, "同步起点须在最近 87 天内，起止时间须有效且不能晚于当前时间。"
                )
            accounts = (
                [await asyncio.to_thread(self.accounts.row, account_id)]
                if account_id
                else await asyncio.to_thread(self.accounts.repository.list_exchange_accounts)
            )
            scanned, added, warnings = 0, 0, []
            previous = await asyncio.to_thread(self.repository.sync_state)
            for row in accounts:
                old = next((item for item in previous if item["account_id"] == row["id"]
                            and item["environment"] == row["environment"]), {})
                state = {
                    **old,
                    "account_id": row["id"],
                    "environment": row["environment"],
                    "complete": old.get("complete", False),
                    "status": old.get("status", "pending"),
                    "message": old.get("message", "尚未完成订单历史同步。"),
                    "cashflows": dict(old.get("cashflows", {})),
                }
                floor = now - 87 * DAY_MS
                cash = state["cashflows"]
                cash.setdefault("next_ms", floor)
                # Resume within the supported lookback without claiming the expired gap was read.
                cash["next_ms"] = max(cash["next_ms"], floor)
                cash["lookback_floor_ms"] = floor
                cash.update(records_scanned=0, records_added=0)
                income_start = explicit_start if explicit_start is not None else max(
                    floor, min(cash["next_ms"], end_ms) - DAY_MS
                )
                try:
                    account = await asyncio.to_thread(self.accounts.private, row["id"])
                    await self._sync_income(
                        account, income_start, end_ms, state
                    )
                except Exception:  # noqa: BLE001 - Never expose signed requests or credentials.
                    cash.update(status="failed", requested_complete=False,
                                message="资金流水同步失败；已保存记录和已确认区间保留，将从检查点重试。")
                    warnings.append(f"账户 {row['id']}：{cash['message']}")
                    await asyncio.to_thread(self.repository.save_sync, **state)
                scanned += cash["records_scanned"]
                added += cash["records_added"]
                option_state = state.setdefault("options", {})
                option_state.pop("records_scanned", None)
                option_state.pop("records_added", None)
                try:
                    account = await asyncio.to_thread(self.accounts.private, row["id"])
                    option_scanned, option_added = await sync_options(
                        self.repository, account, state, start_ms, end_ms, cashflows_only=cashflows_only
                    )
                    scanned += option_scanned
                    added += option_added
                except Exception as exc:  # noqa: BLE001 - Never expose private endpoint or credential failures.
                    option_state = state.setdefault("options", {})
                    scanned += option_state.get("records_scanned", 0)
                    added += option_state.get("records_added", 0)
                    message = str(exc) if isinstance(exc, QueryError) else "期权同步未完成。"
                    option_state.update(status="failed", message=message + " 已保存操作与初始基准保留，下轮重试。")
                    warnings.append(f"账户 {row['id']}：{option_state['message']}")
                    await asyncio.to_thread(self.repository.save_sync, **state)
                if cashflows_only:
                    continue
                state.update(requested_start_ms=start_ms, requested_end_ms=end_ms, complete=False)
                try:
                    account = await asyncio.to_thread(self.accounts.private, row["id"])
                    records = await operation_history(
                        account, lookback_minutes=max(1, math.ceil((now - start_ms) / 60_000))
                    )
                    for record in records:
                        if start_ms <= record["timestamp"] <= end_ms:
                            scanned += 1
                            result = await asyncio.to_thread(self.repository.ingest, record)
                            added += int(result["created"])
                    # Adapter reports are observed evidence, not a guarantee of exhaustive history.
                    state.update(
                        last_success_at=int(time.time() * 1000),
                        status="partial",
                        message="已合并原生执行状态与真实成交；历史保留期、可发现品种及 Python 缓存投影范围有限，不能保证全量历史。",
                    )
                    warnings.append(f"账户 {row['id']}：{state['message']}")
                except Exception:  # noqa: BLE001 - Never expose credentials or raw adapter failures.
                    state.update(
                        status="failed",
                        message="同步失败或事件解析不完整，已保存的记录保留，可重试。",
                    )
                    warnings.append(f"账户 {row['id']}：{state['message']}")
                    logger.warning("Operation reconciliation incomplete for account %s", row["id"])
                await asyncio.to_thread(self.repository.save_sync, **state)
            return {
                "records_scanned": scanned,
                "records_added": added,
                "warnings": warnings,
                "sync": await asyncio.to_thread(self.repository.sync_state, account_id),
            }

    async def worker(self, interval=300):
        while True:
            try:
                await self.sync()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("Operation reconciliation cycle failed; will retry")
            await asyncio.sleep(interval)
