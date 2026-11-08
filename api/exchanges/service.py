"""Frontend-compatible portfolio composition over native connector snapshots."""

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import HTTPException

from engine.connectors.binance import assets as asset_queries
from engine.connectors.binance import queries

from ..engine_client import account_snapshot
from ..operations.options import position_view
from .contracts import ExchangePortfolioResponse


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
    def __init__(self, accounts, portfolio_root=None, operations_repository=None, engine=None):
        self.accounts = accounts
        self.engine = engine
        self.cache, self.locks = {}, {}
        self.catalog_cache, self.catalog_locks = {}, {}
        self.asset_cache, self.asset_locks = {}, {}
        self.gate = asyncio.Semaphore(2)
        self.portfolio_root = Path(portfolio_root) if portfolio_root is not None else None
        self.portfolio_locks = {}
        self.operations_repository = operations_repository

    def portfolio_revision(self, row):
        return hashlib.sha256(json.dumps([
            row[key] for key in ("id", "exchange", "account_name", "environment", "credentials_encrypted", "updated_at")
        ]).encode()).hexdigest()

    def read_portfolio(self, account_id, revision):
        try:
            saved = json.loads((self.portfolio_root / f"{account_id}.json").read_text())
            if saved["revision"] != revision or saved["version"] != 1:
                return None
            if not isinstance(saved["saved_at"], int):
                return None
            saved["portfolio"] = ExchangePortfolioResponse.model_validate(saved["portfolio"]).model_dump()
            return saved
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            return None

    async def persist_portfolio(self, account_id, *, fresh=False):
        async with self.portfolio_locks.setdefault(account_id, asyncio.Lock()):
            self.portfolio_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with (self.portfolio_root / f"{account_id}.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return
                row = await asyncio.to_thread(self.accounts.row, account_id)
                revision = self.portfolio_revision(row)
                saved = await asyncio.to_thread(self.read_portfolio, account_id, revision)
                if not fresh and saved and int(time.time()) // 60 == saved["saved_at"] // 60:
                    return
                value = await self.portfolio(account_id, fresh=fresh)
                if revision != self.portfolio_revision(await asyncio.to_thread(self.accounts.row, account_id)):
                    return
                # The revision binds the display snapshot to this exact account configuration.
                saved = {"version": 1, "revision": revision, "saved_at": int(time.time()), "portfolio": value}
                # Complete the small atomic file write before cancellation can release the writer lock.
                self.write_portfolio(account_id, saved)

    def write_portfolio(self, account_id, saved):
        path = self.portfolio_root / f"{account_id}.json"
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as file:
                os.chmod(temporary, 0o600)
                json.dump(saved, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    async def persisted_portfolio(self, account_id, *, fresh=False):
        if fresh:
            await self.persist_portfolio(account_id, fresh=True)
        row = await asyncio.to_thread(self.accounts.row, account_id)
        saved = await asyncio.to_thread(self.read_portfolio, account_id, self.portfolio_revision(row))
        if saved is None:
            value = {"checked_at": int(time.time()), "account": summary({}), "assets": [],
                    "positions": [], "trades": [], "valuation_source": "exchange",
                    "errors": {"account": "尚无账户快照，等待后台分钟采集。"}, "warnings": {}}
        else:
            value = saved["portfolio"]
        if saved and (time.time() - saved["saved_at"] >= 120 or saved["saved_at"] > time.time()):
            value["warnings"]["account"] = "账户快照已过期，正在显示上次采集数据。 " + value["warnings"].get("account", "")
            for asset in value["assets"]:
                asset["stale"] = True
        if self.operations_repository is not None:
            positions, warning = await asyncio.to_thread(position_view, self.operations_repository, row)
            value["positions"].extend(positions)
            value["warnings"]["positions"] = " ".join(filter(None, [value["warnings"].get("positions"), warning]))
        return value

    async def portfolio_worker(self):
        while True:
            started = time.monotonic()
            try:
                accounts = await asyncio.to_thread(self.accounts.navigation)
                for account in accounts:
                    try:
                        await self.persist_portfolio(account["id"])
                    except Exception:  # noqa: BLE001 - Keep collection alive without logging private payloads.
                        # Do not log connector payloads or private account configuration.
                        logging.getLogger(__name__).warning("账户 %s 分钟快照更新失败，将稍后重试。", account["id"])
            except Exception:  # noqa: BLE001 - Storage errors may include private connection details.
                logging.getLogger(__name__).warning("账户分钟快照采集失败，将稍后重试。")
            await asyncio.sleep(max(1, 60 - (time.monotonic() - started)))

    def invalidate(self, account_id):
        self.cache.pop(account_id, None)
        self.asset_cache.pop(account_id, None)

    async def snapshot(self, account_id, *, fresh=False):
        async with self.locks.setdefault(account_id, asyncio.Lock()):
            account = await asyncio.to_thread(self.accounts.private, account_id)
            identity = tuple(
                account[key]
                for key in (
                    "environment",
                    "credentials_encrypted",
                    "updated_at",
                )
            )
            cached = self.cache.get(account_id)
            if not fresh and cached and cached[0] > time.monotonic() and cached[2] == identity:
                return cached[1]
            async with self.gate:
                value = await account_snapshot(account, self.engine)
            current = await asyncio.to_thread(self.accounts.private, account_id)
            if identity != tuple(
                current[key]
                for key in (
                    "environment",
                    "credentials_encrypted",
                    "updated_at",
                )
            ):
                raise queries.QueryError("账户配置已变化，请重新查询。")
            self.cache[account_id] = (time.monotonic() + 10, value, identity)
            return value

    async def catalog(self, env, product="futures_um"):
        key = (env, product)
        async with self.catalog_locks.setdefault(key, asyncio.Lock()):
            cached = self.catalog_cache.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            if product == "options":
                if env != "production":
                    raise ValueError("期权数据仅使用正式公开行情")
                from engine.connectors.binance.options import markets
                values = await asyncio.to_thread(markets)
                self.catalog_cache[key] = (time.monotonic() + 300, values)
                return values
            if product != "futures_um":
                raise ValueError("不支持的行情产品")
            instruments = await queries.instruments(env)
            values = []
            for instrument in instruments:
                quote = str(instrument.quote_currency)
                symbol = str(instrument.id.symbol).removesuffix("-PERP")
                if (quote not in {"USDT", "USDC"} or str(instrument.settlement_currency) != quote
                        or not symbol.endswith(quote)):
                    continue
                # The native identifier retains Binance's exact contract name even
                # when its base currency is missing or aliases a quantity prefix.
                base = symbol[:-len(quote)]
                values.append({"symbol": f"{base}/{quote}:{instrument.settlement_currency}",
                               "market_type": "linear", "base": base, "quote": quote,
                               "expiry": None})
            values.sort(key=lambda item: item["symbol"])
            self.catalog_cache[key] = (time.monotonic() + 300, values)
            return values

    async def asset_values(self, account_id, *, fresh=False):
        async with self.asset_locks.setdefault(account_id, asyncio.Lock()):
            account = await asyncio.to_thread(self.accounts.private, account_id)
            identity = tuple(account[k] for k in ("environment", "credentials_encrypted", "updated_at"))
            cached = self.asset_cache.get(account_id)
            if cached is None or cached["identity"] != identity:
                cached = {"identity": identity, "expires": 0, "prices": {}}
                self.asset_cache[account_id] = cached
            if fresh or cached["expires"] <= time.monotonic():
                async with self.gate:
                    cached["evidence"] = await asyncio.to_thread(asset_queries.asset_account, account)
                cached["expires"] = time.monotonic() + 10
            evidence = cached["evidence"]
            # USD indices share the same denominator. Do not assume USDC/USD or USDT/USD = 1.
            currencies = {"USDT", *evidence["assets"]}
            rates, rows = {}, []
            for currency in sorted(currencies):
                amount = evidence["assets"].get(currency, {}).get("walletBalance", Decimal(0))
                previous = cached["prices"].get(currency)
                if (fresh or previous is None or previous["amount"] != amount
                        or previous["expires"] <= time.monotonic()):
                    try:
                        async with self.gate:
                            quote = await asyncio.to_thread(asset_queries.asset_price, account["environment"], currency)
                        # The threshold is applied below after conversion to USDT.
                        previous = {**quote, "amount": amount, "fetched_at": time.monotonic(), "expires": time.monotonic() + 60,
                                    "error": None}
                    except queries.QueryError as exc:
                        previous = {**(previous or {}), "amount": amount,
                                    "expires": time.monotonic() + 60, "error": str(exc)}
                    cached["prices"][currency] = previous
                rates[currency] = previous
            denominator = rates["USDT"]
            valid_denominator = denominator.get("price") is not None and not denominator["error"]
            for currency, values in evidence["assets"].items():
                quote = rates[currency]
                price = (quote["price"] / denominator["price"]
                         if quote.get("price") is not None and denominator.get("price") is not None else None)
                stale = bool(quote["error"] or denominator["error"])
                value = values["walletBalance"] * price if price is not None else None
                small = value is not None and abs(value) < 5
                if not stale and small and currency != "USDT":
                    # Keep dust prices for one hour, without sliding the expiry on reads.
                    quote["expires"] = quote["fetched_at"] + 3600
                elif not stale:
                    quote["expires"] = quote["fetched_at"] + 60
                rows.append({"account_id": account_id, "currency": currency,
                             "balance": text(values["walletBalance"]), "price_usdt": text(price),
                             "value_usdt": text(value), "priced_at": min(quote.get("priced_at", 0), denominator.get("priced_at", 0)) or None,
                             "small": small, "stale": stale,
                             "error": quote["error"] or denominator["error"]})
            current = await asyncio.to_thread(self.accounts.private, account_id)
            if identity != tuple(current[k] for k in ("environment", "credentials_encrypted", "updated_at")):
                self.asset_cache.pop(account_id, None)
                raise queries.QueryError("账户配置已变化，请重新查询。")
            if evidence["multi_assets"] and valid_denominator:
                result = summary({key: text(number(value) / denominator["price"])
                                  for key, value in evidence["totals"].items()})
                source = "exchange"
            elif valid_denominator and all(not row["stale"] and row["value_usdt"] is not None for row in rows):
                totals = {"total_wallet_balance": Decimal(0), "total_unrealized_profit": Decimal(0)}
                for row in rows:
                    values = evidence["assets"][row["currency"]]
                    rate = number(row["price_usdt"])
                    totals["total_wallet_balance"] += values["walletBalance"] * rate
                    totals["total_unrealized_profit"] += values["unrealizedProfit"] * rate
                result = summary(totals)
                source = "estimate"
            else:
                result, source = summary({}), "estimate"
            return result, rows, source, evidence["observed_at"]

    async def portfolio(self, account_id, *, fresh=False):
        row = await asyncio.to_thread(self.accounts.row, account_id)
        snapshot = await self.snapshot(account_id, fresh=fresh)
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
        errors, warnings = {}, {}
        assets = [{"account_id": account_id, "currency": "USDT", "balance": account["wallet_balance"],
                   "price_usdt": "1", "value_usdt": account["wallet_balance"], "priced_at": snapshot.observed_at,
                   "small": account["wallet_balance"] is not None and abs(number(account["wallet_balance"])) < 5,
                   "stale": False, "error": None}] if account["wallet_balance"] is not None else []
        source, checked_at = "exchange", snapshot.observed_at
        if any(
            currency != "USDT" and number(amount) != 0
            for currency, amount in snapshot.balances.items()
        ) or any(not position["symbol"].endswith(":USDT") for position in snapshot.positions):
            try:
                account, assets, source, checked_at = await self.asset_values(account_id, fresh=fresh)
                warnings["account"] = ("按交易所 USD 汇总折算 USDT。" if source == "exchange" else
                                       "按资产指数估值，含小额资产；估值不用于保证金或交易风控。")
                if any(item["stale"] or item["value_usdt"] is None for item in assets):
                    warnings["account"] += " 部分资产暂未估值，保留的旧价仅供参考。"
            except queries.QueryError as exc:
                account, assets = summary({}), []
                errors["account"] = str(exc)
        if any(account[k] is None for k in ("total_equity", "wallet_balance", "unrealized_pnl")):
            errors.setdefault("account", "账户权益数据不完整：资产价格暂不可用或缺少钱包余额、未实现盈亏。")
        return {
            "checked_at": checked_at,
            "account": account,
            "assets": [{**item, "account_name": row["account_name"]} for item in assets],
            "valuation_source": source,
            "positions": positions,
            "trades": [],
            "errors": errors,
            "warnings": {
                "account": warnings.get("account", "原生快照尚未提供仓位估值与实际杠杆。"),
                "positions": "原生标准仓位未提供标记价、强平价、逐仓模式等字段；缺失值显示为 --。",
            },
        }

    async def overview(self, *, fresh=False):
        accounts = await asyncio.to_thread(self.accounts.navigation)
        values = []
        errors, assets, valuation_warnings, positions, position_warnings = [], [], [], [], []
        for account in accounts:
            try:
                value = await self.persisted_portfolio(account["id"], fresh=fresh)
                assets.extend(value["assets"])
                positions.extend(value["positions"])
                if value["warnings"].get("positions"):
                    position_warnings.append(f"{account['account_name']}: {value['warnings']['positions']}")
                if value["errors"]:
                    errors.append(f"{account['account_name']}: {value['errors']['account']}")
                else:
                    values.append(value)
                    if value["valuation_source"] == "estimate" or any(a["stale"] for a in value["assets"]):
                        valuation_warnings.append(f"{account['account_name']}: {value['warnings']['account']}")
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
        if valuation_warnings:
            warnings["account"] = "；".join(filter(None, [warnings.get("account"), *valuation_warnings]))
        return {
            "checked_at": min((v["checked_at"] for v in values), default=int(time.time())),
            "account": summary(info),
            "assets": assets,
            "valuation_source": "estimate" if any(v["valuation_source"] == "estimate" for v in values) else "exchange",
            "positions": positions,
            "trades": [],
            "errors": {"account": "；".join(errors)} if errors and not values else {},
            "warnings": {"positions": "；".join(position_warnings) or "原生标准仓位的缺失字段未做推算。", **warnings},
        }
