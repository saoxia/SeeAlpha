"""Project-owned ledger ingestion and bounded native reconciliation reads."""

import asyncio
import logging
import math
import time

from connectors.binance.operations import operation_history

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
        from connectors.binance.operations import normalize_event

        record = normalize_event(event, order, account)
        return self.repository.ingest(record) if record is not None else None

    async def sync(self, account_id=None, start_ms=None, end_ms=None):
        if self.lock.locked():
            from fastapi import HTTPException

            raise HTTPException(409, "已有操作记录同步正在运行。")
        async with self.lock:
            now = int(time.time() * 1000)
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
            for row in accounts:
                state = {
                    "account_id": row["id"],
                    "environment": row["environment"],
                    "requested_start_ms": start_ms,
                    "requested_end_ms": end_ms,
                    "complete": False,
                }
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
