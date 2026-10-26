"""数据管道共享的数据模型和 Binance/Nautilus 命名规则。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

SUPPORTED_MARKET = "futures_um"
SUPPORTED_DATA_TYPES = ("trades",)
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


class PipelineConfigError(ValueError):
    """表示管道配置或请求不受支持。"""


def as_utc(value: datetime) -> datetime:
    """将 datetime 规范化为 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_symbol(symbol: str) -> str:
    """把 BTC-USDT、BTC/USDT 等用户写法规范化为 Binance 的 BTCUSDT。"""
    normalized = symbol.strip().upper().replace("-", "").replace("/", "").replace("_", "")
    if not normalized or not normalized.endswith("USDT") or not normalized[:-4].isalnum():
        raise PipelineConfigError(f"无效的 USDT 交易对：{symbol!r}")
    return normalized


def interval_delta(interval: str) -> timedelta:
    """返回受支持 Binance 周期对应的时间长度。"""
    try:
        return INTERVALS[interval][2]
    except KeyError as exc:
        supported = ", ".join(INTERVALS)
        raise PipelineConfigError(f"暂不支持周期 {interval!r}；可用值：{supported}") from exc


def instrument_id_for(symbol: str, market: str = SUPPORTED_MARKET) -> str:
    """返回 Binance 市场对应的 Nautilus InstrumentId。"""
    if market != SUPPORTED_MARKET:
        raise PipelineConfigError("首版管道仅支持 Binance USD-M 永续合约")
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


@dataclass(frozen=True)
class PipelineSettings:
    """从 TOML 读取的项目级管道设置。"""

    market: str
    data_type: str
    symbols: tuple[str, ...]
    intervals: tuple[str, ...]
    raw_dir: Path
    catalog_dir: Path
    state_dir: Path


@dataclass(frozen=True)
class DataRequest:
    """一次历史数据准备请求，时间范围采用左闭右开语义。"""

    symbols: tuple[str, ...]
    start: datetime
    end: datetime
    data_type: str = "trades"
    bar_intervals: tuple[str, ...] | None = None
    market: str = SUPPORTED_MARKET

    def __post_init__(self) -> None:
        normalized_symbols = tuple(
            dict.fromkeys(normalize_symbol(symbol) for symbol in self.symbols)
        )
        object.__setattr__(self, "symbols", normalized_symbols)
        object.__setattr__(self, "start", as_utc(self.start))
        object.__setattr__(self, "end", as_utc(self.end))
        if self.bar_intervals is not None:
            normalized_intervals = tuple(dict.fromkeys(self.bar_intervals))
            for interval in normalized_intervals:
                interval_delta(interval)
            object.__setattr__(self, "bar_intervals", normalized_intervals)

        if not normalized_symbols:
            raise PipelineConfigError("至少需要一个交易对")
        if self.start >= self.end:
            raise PipelineConfigError("开始时间必须早于结束时间")
        if self.market != SUPPORTED_MARKET:
            raise PipelineConfigError("首版管道仅支持 futures_um")
        if self.data_type not in SUPPORTED_DATA_TYPES:
            raise PipelineConfigError(
                f"暂不支持数据类型 {self.data_type!r}；可用值：{', '.join(SUPPORTED_DATA_TYPES)}",
            )


@dataclass(frozen=True)
class ArchiveSpec:
    """一个 Binance 日归档及其覆盖范围。"""

    symbol: str
    data_type: str
    day: date
    source_path: str
    interval: str | None = None


@dataclass(frozen=True)
class SyncedArchive:
    """已同步到本地的归档。"""

    spec: ArchiveSpec
    path: Path


@dataclass(frozen=True)
class PreparedDataset:
    """已通过准备和校验、可从 Catalog 加载的数据集描述。"""

    catalog_path: Path
    data_type: str
    instrument_ids: tuple[str, ...]
    bar_types: tuple[str, ...]
    start: datetime
    end: datetime
    ready: bool
    feature_bar_types: tuple[str, ...] = ()
