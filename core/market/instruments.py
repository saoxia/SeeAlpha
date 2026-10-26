"""Shared Binance USD-M naming and interval rules."""

from datetime import UTC, datetime, timedelta

SUPPORTED_MARKET = "futures_um"
INTERVALS: dict[str, tuple[int, str, timedelta]] = {
    "1s": (1, "SECOND", timedelta(seconds=1)),
    "1m": (1, "MINUTE", timedelta(minutes=1)),
    "3m": (3, "MINUTE", timedelta(minutes=3)),
    "5m": (5, "MINUTE", timedelta(minutes=5)),
    "15m": (15, "MINUTE", timedelta(minutes=15)),
    "30m": (30, "MINUTE", timedelta(minutes=30)),
    "1h": (1, "HOUR", timedelta(hours=1)),
    "2h": (2, "HOUR", timedelta(hours=2)),
    "4h": (4, "HOUR", timedelta(hours=4)),
    "6h": (6, "HOUR", timedelta(hours=6)),
    "8h": (8, "HOUR", timedelta(hours=8)),
    "12h": (12, "HOUR", timedelta(hours=12)),
    "1d": (1, "DAY", timedelta(days=1)),
}


class MarketConfigError(ValueError):
    """表示市场命名或周期不受支持。"""


def as_utc(value: datetime) -> datetime:
    """将 datetime 规范化为 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_symbol(symbol: str) -> str:
    """把 BTC-USDT、BTC/USDT 等用户写法规范化为 Binance 的 BTCUSDT。"""
    normalized = symbol.strip().upper().replace("-", "").replace("/", "").replace("_", "")
    if not normalized or not normalized.endswith("USDT") or not normalized[:-4].isalnum():
        raise MarketConfigError(f"无效的 USDT 交易对：{symbol!r}")
    return normalized


def interval_delta(interval: str) -> timedelta:
    """返回受支持 Binance 周期对应的时间长度。"""
    try:
        return INTERVALS[interval][2]
    except KeyError as exc:
        supported = ", ".join(INTERVALS)
        raise MarketConfigError(f"暂不支持周期 {interval!r}；可用值：{supported}") from exc


def instrument_id_for(symbol: str, market: str = SUPPORTED_MARKET) -> str:
    """返回 Binance 市场对应的 Nautilus InstrumentId。"""
    if market != SUPPORTED_MARKET:
        raise MarketConfigError("首版管道仅支持 Binance USD-M 永续合约")
    return f"{symbol.upper()}-PERP.BINANCE"


def bar_type_for(symbol: str, interval: str, market: str = SUPPORTED_MARKET) -> str:
    """返回与 Binance 外部 K 线一致的 Nautilus BarType。"""
    instrument_id = instrument_id_for(symbol, market)
    try:
        step, aggregation, _ = INTERVALS[interval]
    except KeyError as exc:
        interval_delta(interval)
        raise AssertionError("unreachable") from exc
    return f"{instrument_id}-{step}-{aggregation}-LAST-EXTERNAL"
