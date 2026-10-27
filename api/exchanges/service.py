"""Frontend-compatible portfolio composition over native connector snapshots."""

import asyncio
import time
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

from connectors.binance import queries


def number(value):
    if value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def text(value):
    return format(value, "f") if value is not None else None


def summary(info):
    equity = number(info.get("total_margin_balance"))
    wallet = number(info.get("total_wallet_balance"))
    pnl = number(info.get("total_unrealized_profit"))
    maintenance = number(info.get("total_maint_margin"))
    if wallet is not None and pnl is not None:
        calculated = wallet + pnl
        if equity is not None and abs(equity - calculated) > Decimal("0.00000001"):
            raise queries.QueryError("账户权益与钱包余额、浮盈亏不一致，拒绝发布。")
        equity = calculated
    return {
        "currency": "USDT",
        "total_equity": text(equity),
        "wallet_balance": text(wallet),
        "unrealized_pnl": text(pnl),
        "maintenance_margin": text(maintenance),
        "margin_ratio": text(maintenance / equity * 100)
        if maintenance is not None and equity is not None and equity > 0
        else None,
        "position_value": None,
        "effective_leverage": None,
    }


class ExchangeService:
    def __init__(self, accounts):
        self.accounts = accounts
        self.cache, self.locks = {}, {}
        self.catalog_cache, self.catalog_locks = {}, {}
        self.gate = asyncio.Semaphore(2)

    def invalidate(self, account_id):
        self.cache.pop(account_id, None)

    async def snapshot(self, account_id, *, fresh=False):
        async with self.locks.setdefault(account_id, asyncio.Lock()):
            account = await asyncio.to_thread(self.accounts.private, account_id)
            identity = tuple(
                account[key]
                for key in (
                    "environment",
                    "credentials_encrypted",
                    "market_symbols_json",
                    "updated_at",
                )
            )
            cached = self.cache.get(account_id)
            if not fresh and cached and cached[0] > time.monotonic() and cached[2] == identity:
                return cached[1]
            async with self.gate:
                value = await queries.account_snapshot(account)
            current = await asyncio.to_thread(self.accounts.private, account_id)
            if identity != tuple(
                current[key]
                for key in (
                    "environment",
                    "credentials_encrypted",
                    "market_symbols_json",
                    "updated_at",
                )
            ):
                raise queries.QueryError("账户配置已变化，请重新查询。")
            self.cache[account_id] = (time.monotonic() + 10, value, identity)
            return value

    async def catalog(self, env):
        async with self.catalog_locks.setdefault(env, asyncio.Lock()):
            cached = self.catalog_cache.get(env)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            instruments = await queries.instruments(env)
            values = [
                {
                    "symbol": f"{i.base_currency}/{i.quote_currency}:{i.settlement_currency}",
                    "market_type": "linear",
                    "base": str(i.base_currency),
                    "quote": str(i.quote_currency),
                    "expiry": None,
                }
                for i in instruments
                if str(i.settlement_currency) == "USDT"
            ]
            values.sort(key=lambda item: item["symbol"])
            self.catalog_cache[env] = (time.monotonic() + 300, values)
            return values

    async def portfolio(self, account_id):
        row = await asyncio.to_thread(self.accounts.row, account_id)
        snapshot = await self.snapshot(account_id)
        account = summary(snapshot.info)
        positions = [
            {
                **p,
                "account_id": account_id,
                "account_name": row["account_name"],
                "exchange": row["exchange"],
            }
            for p in snapshot.positions
        ]
        errors = {}
        if any(
            currency != "USDT" and number(amount) != 0
            for currency, amount in snapshot.balances.items()
        ):
            account = summary({})
            errors["account"] = "非 USDT 抵押资产的汇总计价尚未验收；余额可分币种查询。"
        if any(account[k] is None for k in ("total_equity", "wallet_balance", "unrealized_pnl")):
            errors.setdefault("account", "当前原生账户快照缺少完整权益字段。")
        return {
            "checked_at": snapshot.observed_at,
            "account": account,
            "positions": positions,
            "trades": [],
            "errors": errors,
            "warnings": {
                "account": "原生快照尚未提供仓位估值与实际杠杆。",
                "positions": "原生标准仓位未提供标记价、强平价、逐仓模式等字段；缺失值显示为 --。",
            },
        }

    async def overview(self):
        accounts = await asyncio.to_thread(self.accounts.navigation)
        values = []
        errors = []
        for account in accounts:
            try:
                value = await self.portfolio(account["id"])
                if value["errors"]:
                    errors.append(f"{account['account_name']}: 账户数据不完整。")
                else:
                    values.append(value)
            except queries.QueryError:
                errors.append(f"{account['account_name']}: Binance 查询失败。")
            except HTTPException as exc:
                errors.append(f"{account['account_name']}: {exc.detail}")
        info = {}
        for source, key in (
            ("total_margin_balance", "total_equity"),
            ("total_wallet_balance", "wallet_balance"),
            ("total_unrealized_profit", "unrealized_pnl"),
            ("total_maint_margin", "maintenance_margin"),
        ):
            if values:
                info[source] = (
                    text(sum((number(v["account"][key]) for v in values), Decimal(0)))
                    if all(v["account"][key] is not None for v in values)
                    else None
                )
        warnings = {"account": "部分账户未计入：" + "；".join(errors)} if errors and values else {}
        return {
            "checked_at": min((v["checked_at"] for v in values), default=int(time.time())),
            "account": summary(info),
            "positions": [p for v in values for p in v["positions"]],
            "trades": [],
            "errors": {"account": "；".join(errors)} if errors and not values else {},
            "warnings": {"positions": "原生标准仓位的缺失字段未做推算。", **warnings},
        }
