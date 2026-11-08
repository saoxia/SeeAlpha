"""API-owned valuation collection, reconciliation and replay lifecycle."""

import asyncio
import fcntl
import hashlib
import logging
import time
import uuid
from contextlib import aclosing, suppress

from fastapi import HTTPException

from connectors.binance import assets, queries
from connectors.binance.valuation import valuation_events

from .repository import EquityRepository
from .valuation import decimal, reconcile

logger = logging.getLogger(__name__)


def identity(row):
    return hashlib.sha256((row["environment"] + row["credentials_encrypted"]).encode()).hexdigest()


def account_state(evidence, state):
    multipliers = {p["instrument"]: p.get("multiplier", "1") for p in state["positions"]}
    positions = [{**p, "multiplier": multipliers.get(p["instrument"], "1"),
                  "quantity": str(decimal(p["quantity"]) * decimal(multipliers.get(p["instrument"], "1")))}
                 for p in evidence["positions"]]
    return {**state, "wallet": {currency: str(item["walletBalance"])
                                for currency, item in evidence["assets"].items()},
            "positions": positions, "account_time": evidence["observed_at"] * 1000}


class EquityService:
    def __init__(self, database, accounts):
        self.repository = EquityRepository(database)
        self.accounts = accounts
        self.owner = None
        self.collectors = {}
        self.jobs = set()
        self.task = None

    async def start(self, path):
        self.repository.require()
        path.parent.mkdir(parents=True, exist_ok=True)
        owner = path.open("a+")
        try:
            fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            owner.close()
            return
        self.owner = owner
        await asyncio.to_thread(self.repository.recover)
        self.task = asyncio.create_task(self.worker())

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        for _, task in self.collectors.values():
            task.cancel()
        await asyncio.gather(*(task for _, task in self.collectors.values()), return_exceptions=True)
        # A database replay is atomic and runs on a worker thread. Join it before
        # releasing ownership; cancelling to_thread would leave its transaction alive.
        await asyncio.gather(*self.jobs, return_exceptions=True)
        if self.owner is not None:
            self.owner.close()
            self.owner = None

    async def worker(self):
        while True:
            try:
                rows = await asyncio.to_thread(self.accounts.repository.list_exchange_accounts)
                wanted = {row["id"]: row for row in rows if row["exchange"] == "binance"}
                for account_id, (old, task) in list(self.collectors.items()):
                    if account_id not in wanted or old != identity(wanted[account_id]) or task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        del self.collectors[account_id]
                for account_id, row in wanted.items():
                    if account_id not in self.collectors:
                        self.collectors[account_id] = (identity(row), asyncio.create_task(self.collect(row)))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - Never log credentials or raw database errors.
                logger.warning("Asset collector configuration refresh failed; will retry")
            await asyncio.sleep(15)

    async def collect(self, row):
        account_id, environment = row["id"], row["environment"]
        while True:
            stream, sequence = uuid.uuid4().hex, 0
            last_check, breach = time.monotonic(), False
            fx = {}

            async def persist(evidence, source_stream=stream):
                nonlocal sequence
                sequence += 1
                await asyncio.to_thread(self.repository.append, account_id, environment,
                                         f"{source_stream}:{sequence}", evidence)

            try:
                account = await asyncio.to_thread(self.accounts.private, account_id)
                if identity(account) != identity(row):
                    return
                # Each stream obtains a fresh REST baseline. Mark restarts and
                # credential changes rather than implying uninterrupted coverage.
                await persist({"time": int(time.time() * 1000), "reason": "gap", "state": {}})
                async with aclosing(valuation_events(account)) as events:
                    async for event in events:
                        currencies = {currency for currency, amount in event["wallet"].items() if decimal(amount)}
                        currencies.update(p["currency"] for p in event["positions"])
                        foreign = currencies - {"USDT"}
                        if foreign:
                            for currency in foreign | {"USDT"}:
                                old = fx.get(currency)
                                if old is None or event["time"] - old["time"] >= 60_000:
                                    try:
                                        quote = await asyncio.to_thread(assets.asset_price, environment, currency)
                                        fx[currency] = {"price": str(quote["price"]), "time": quote["priced_at"] * 1000}
                                    except queries.QueryError:
                                        pass  # Old evidence keeps its original expiry; no zero fallback.
                        rates = {}
                        if foreign and "USDT" in fx:
                            for currency in foreign:
                                if currency in fx:
                                    rates[currency] = {
                                        "price": str(decimal(fx[currency]["price"]) / decimal(fx["USDT"]["price"])),
                                        "time": min(fx[currency]["time"], fx["USDT"]["time"]),
                                    }
                        evidence = {"time": event["time"], "reason": event["reason"],
                                    "state": {"wallet": event["wallet"], "positions": event["positions"],
                                              "account_time": event.get("account_time", event["time"]),
                                              "marks": event["marks"], "fx": rates}}
                        await persist(evidence)
                        if time.monotonic() - last_check >= 300:
                            # An independent GET avoids opening/closing a shared private
                            # listenKey. Compare at the same saved marks and FX.
                            snapshot = await asyncio.to_thread(assets.asset_account, account, positions=True)
                            observed = {**evidence, "reason": "check",
                                        "state": account_state(snapshot, evidence["state"])}
                            check = reconcile(evidence, observed, breach)
                            breach = check["breach"]
                            last_check = time.monotonic()
                            if check["calibrate"]:
                                observed.update(reason="calibration", time=int(time.time() * 1000),
                                                reconciliation={**check, "source_observed_at": snapshot["observed_at"]})
                                await persist(observed)
                                break  # Establish a fresh account/price baseline after calibration.
                            await persist({**evidence, "reason": "check", "reconciliation": {
                                **check, "source_observed_at": snapshot["observed_at"]}})
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - Sanitized health is persisted; no raw account/network errors.
                logger.warning("Asset event collection interrupted for account %s", account_id)
            finally:
                # Also invalidate the last good point on cancellation/shutdown.
                with suppress(Exception):
                    await persist({"time": int(time.time() * 1000), "reason": "gap", "state": {}})
            await asyncio.sleep(5)

    async def rebuild(self, row, start, end):
        if self.owner is None:
            raise HTTPException(409, "本进程未持有资产采集与重算锁，请向持锁进程发起请求。")
        job = await asyncio.to_thread(self.repository.create_rebuild, row["id"], row["environment"], start, end)

        async def run():
            try:
                await asyncio.to_thread(self.repository.rebuild, job["id"])
            except Exception:  # noqa: BLE001 - The repository saves a sanitized failure.
                logger.warning("Asset replay failed for account %s", row["id"])

        task = asyncio.create_task(run())
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)
        return job
