"""Receive an issued package and preview its target holdings.

The coordinator and the netting arithmetic clip the aggregated target, then
target-net against the current position and submit the delta. Preview uses a
simulated venue. It does not read credentials or send exchange orders.
"""

import base64
import shutil
import tempfile
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.model import AccountType, Bar, BarType, Money, OmsType

from engine.runtime import EngineConflict
from engine.strategies.package import (
    PackageError,
    build_strategy,
    extract,
    load_entry,
    namespace_for,
    parse_metadata,
    prepare_parameters,
    release_namespace,
)
from engine.strategies.portfolio_coordinator import PortfolioCoordinator, PortfolioCoordinatorConfig
from engine.strategies.target_netting import MemberTarget, net_signed_quantity
from engine.strategies.targets import signed_quantity
from nautilus_support import TestInstrumentProvider, instrument_fee_model


def clip_member_targets(targets, risk_limit: Decimal):
    """Scale an aggregate that exceeds the account risk limit. Netting stays unchanged."""
    net = net_signed_quantity(targets)
    if net == 0 or abs(net) <= risk_limit:
        return list(targets)
    scale = risk_limit / abs(net)
    return [
        MemberTarget(target.member_id, target.signed_quantity * scale, target.risk_exit)
        for target in targets
    ]


class RiskClippedCoordinator(PortfolioCoordinator):
    """PortfolioCoordinator with the account risk limit applied before target netting."""

    def __init__(self, config: PortfolioCoordinatorConfig) -> None:
        super().__init__(config)
        self.risk_limit = None

    def _member_targets(self):
        return clip_member_targets(super()._member_targets(), self.risk_limit)


def accept_load(runtime, tenant_id, payload):
    if not isinstance(payload, dict):
        raise EngineConflict("策略下发无效。")
    purpose = payload.get("grant_purpose")
    if purpose not in {"production", "development"}:
        raise EngineConflict("下发用途无效。")
    row = runtime.store.tenant(tenant_id)
    if row is None:
        raise EngineConflict("租户不存在。")
    if row["status"] != "active":
        raise EngineConflict("已归档的租户不能加载策略。")
    if row["purpose"] != purpose:
        if row["purpose"] == "production":
            raise EngineConflict("正式租户只能加载正式授权。")
        raise EngineConflict("下发用途与租户类型不一致。")
    account_id = str(payload.get("account_id") or "")
    issuance_id = str(payload.get("issuance_id") or "")
    version = str(payload.get("version") or "")
    code_key = payload.get("code_key")
    entry_point = payload.get("entry_point")
    package_hash = payload.get("package_hash")
    if not account_id or not issuance_id or not version:
        raise EngineConflict("策略下发缺少账户、编号或版本。")
    if len(account_id) > 40 or len(issuance_id) > 64 or len(version) > 40:
        raise EngineConflict("策略下发字段过长。")
    if not isinstance(code_key, str) or not isinstance(entry_point, str) or not isinstance(package_hash, str):
        raise EngineConflict("策略下发缺少包标识、入口或哈希。")
    try:
        archive = _decode_archive(payload.get("archive"))
        metadata = parse_metadata(archive)
        if metadata.digest != package_hash or metadata.name != code_key or metadata.version != version:
            raise EngineConflict("策略包与下发记录不一致。")
        if metadata.entry_point != entry_point:
            raise EngineConflict("策略包入口与下发记录不一致。")
        parameters = _prepare_archive(
            archive, metadata, payload.get("parameters"), tenant_id, issuance_id,
        )
    except PackageError as exc:
        raise EngineConflict(str(exc)) from exc
    return runtime.store.upsert_strategy_load(
        tenant_id, account_id, issuance_id, code_key, version, purpose, parameters,
        entry_point, package_hash, archive,
    )


def preview_loads(loads, bars):
    if not loads:
        raise EngineConflict("该租户账户没有已下发的策略。")
    rows = _normalize_bars(bars)
    groups = defaultdict(list)
    for load in loads:
        groups[load["parameters"]["symbol"]].append(load)
    holdings = []
    orders = []
    positions = []
    coordinator_ids = []
    member_ids = []
    for symbol, group in groups.items():
        piece = _preview_symbol(symbol, group, rows)
        holdings.extend(piece["holdings"])
        orders.extend(piece["orders"])
        positions.append(piece["position"])
        coordinator_ids.append(piece["coordinator_id"])
        member_ids.extend(piece["member_ids"])
    return {
        "tenant_id": str(loads[0]["tenant_id"]),
        "account_id": str(loads[0]["account_id"]),
        "holdings": holdings,
        "orders": orders,
        "positions": positions,
        "coordinator_ids": coordinator_ids,
        "member_ids": member_ids,
    }


def _preview_symbol(symbol, group, rows):
    instrument = _instrument(symbol)
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    risk_limit = min(Decimal(item["parameters"]["risk_limit"]) for item in group)
    coordinator = RiskClippedCoordinator(
        PortfolioCoordinatorConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            order_id_tag="NET",
            close_positions_on_stop=False,
        ),
    )
    coordinator.risk_limit = risk_limit
    bound = []
    namespaces = []
    directories = []
    engine = None
    try:
        for index, load in enumerate(group):
            strategy, namespace, directory = _load_member(load, instrument, bar_type, f"E{index}")
            namespaces.append(namespace)
            directories.append(directory)
            allocation = Decimal(load["parameters"]["allocation"])

            def sink(strategy, holding, coordinator=coordinator, allocation=allocation, symbol=symbol):
                if holding.symbol != symbol:
                    raise EngineConflict("目标持仓的品种与下发参数不一致。")
                coordinator.report_target(
                    str(strategy.strategy_id), signed_quantity(holding, allocation),
                )

            strategy.target_sink = sink
            bound.append((load, strategy))
        engine = BacktestEngine(BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
        engine.add_venue(
            instrument.id.venue,
            OmsType.NETTING,
            AccountType.MARGIN,
            [Money(100_000, instrument.quote_currency)],
            default_leverage=Decimal(5),
            fee_model=instrument_fee_model(instrument),
        )
        engine.add_instrument(instrument)
        engine.add_strategies([coordinator, *[strategy for _load, strategy in bound]])
        engine.add_data(_nautilus_bars(instrument, bar_type, rows))
        engine.run()
        holdings = []
        member_ids = []
        for load, strategy in bound:
            member_ids.append(str(strategy.strategy_id))
            for holding in strategy.published:
                holdings.append({
                    "issuance_id": str(load["issuance_id"]),
                    "code_key": load["code_key"],
                    "symbol": holding.symbol,
                    "quantity": None if holding.quantity is None else format(holding.quantity, "f"),
                    "weight": None if holding.weight is None else format(holding.weight, "f"),
                })
        position = engine.portfolio.net_position(instrument.id)
        return {
            "holdings": holdings,
            "orders": [_order_view(order) for order in engine.cache.orders()],
            "position": {"symbol": symbol, "quantity": _decimal_text(position)},
            "coordinator_id": str(coordinator.strategy_id),
            "member_ids": member_ids,
        }
    finally:
        if engine is not None:
            engine.dispose()
        for namespace in namespaces:
            release_namespace(namespace)
        for directory in directories:
            shutil.rmtree(directory, ignore_errors=True)


def _decode_archive(value):
    if not isinstance(value, str) or not value:
        raise PackageError("策略包无效。")
    try:
        archive = base64.b64decode(value, validate=True)
    except ValueError:
        raise PackageError("策略包无效。") from None
    return archive


def _prepare_archive(archive, metadata, parameters, tenant_id, issuance_id):
    directory = Path(tempfile.mkdtemp())
    namespace = namespace_for(tenant_id, issuance_id, uuid4().hex)
    try:
        extract(archive, directory)
        module, _strategy_type = load_entry(directory, metadata.entry_point, namespace)
        return prepare_parameters(module, parameters)
    finally:
        release_namespace(namespace)
        shutil.rmtree(directory, ignore_errors=True)


def _load_member(load, instrument, bar_type, order_id_tag):
    archive = load.get("archive")
    if not isinstance(archive, (bytes, bytearray)) or not archive:
        raise EngineConflict("策略下发缺少包内容。")
    directory = Path(tempfile.mkdtemp())
    namespace = namespace_for(load["tenant_id"], load["issuance_id"], uuid4().hex)
    try:
        metadata = extract(archive, directory)
        if (
            metadata.digest != load["package_hash"]
            or metadata.name != load["code_key"]
            or metadata.version != load["version"]
            or metadata.entry_point != load["entry_point"]
        ):
            raise EngineConflict("策略包与下发记录不一致。")
        module, strategy_type = load_entry(directory, metadata.entry_point, namespace)
        parameters = prepare_parameters(module, load["parameters"])
        if parameters != load["parameters"]:
            raise EngineConflict("策略参数与已下发记录不一致。")
        strategy = build_strategy(
            strategy_type, parameters, instrument, bar_type, order_id_tag,
        )
    except PackageError as exc:
        release_namespace(namespace)
        shutil.rmtree(directory, ignore_errors=True)
        raise EngineConflict(str(exc)) from exc
    except Exception:
        release_namespace(namespace)
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return strategy, namespace, directory


def _instrument(symbol):
    if symbol == "ETHUSDT":
        return TestInstrumentProvider.ethusdt_perp_binance()
    raise EngineConflict("该策略品种还不能在引擎里预览。")


def _normalize_bars(bars):
    if not isinstance(bars, list) or not bars or len(bars) > 2000:
        raise EngineConflict("预览需要 1 到 2000 根 K 线。")
    rows = []
    for item in bars:
        if not isinstance(item, dict):
            raise EngineConflict("K 线价格无效。")
        try:
            open_ = Decimal(str(item["open"]))
            high = Decimal(str(item["high"]))
            low = Decimal(str(item["low"]))
            close = Decimal(str(item["close"]))
        except (KeyError, InvalidOperation, ValueError):
            raise EngineConflict("K 线价格无效。") from None
        if min(open_, high, low, close) <= 0 or high < max(open_, close) or low > min(open_, close):
            raise EngineConflict("K 线高低价与开收盘不一致。")
        if not all(price.is_finite() for price in (open_, high, low, close)):
            raise EngineConflict("K 线价格无效。")
        rows.append((open_, high, low, close))
    return rows


def _nautilus_bars(instrument, bar_type, rows):
    bars = []
    for index, (open_, high, low, close) in enumerate(rows):
        opened = 1_700_000_000_000_000_000 + (index + 1) * 60_000_000_000
        bars.append(Bar(
            bar_type,
            _price(instrument, open_),
            _price(instrument, high),
            _price(instrument, low),
            _price(instrument, close),
            instrument.make_qty(Decimal(10)),
            opened,
            opened,
        ))
    return bars


def _price(instrument, value):
    increment = instrument.price_increment.as_decimal()
    steps = (value / increment).to_integral_value(rounding=ROUND_HALF_UP)
    return instrument.make_price(steps * increment)


def _order_view(order):
    side = order.side.name if hasattr(order.side, "name") else str(order.side)
    return {
        "side": side,
        "quantity": format(order.quantity.as_decimal(), "f"),
        "tags": [str(tag) for tag in (order.tags or [])],
        "strategy_id": str(order.strategy_id),
    }


def _decimal_text(position):
    if hasattr(position, "as_decimal"):
        return format(position.as_decimal(), "f")
    return format(Decimal(str(position)), "f")
