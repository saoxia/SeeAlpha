from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DataRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=30)
    product: Literal["futures_um", "options"] = "futures_um"
    start: datetime
    end: datetime
    intervals: list[str] = Field(default_factory=lambda: ["1m"], max_length=16,
                                 description="空列表仅准备 Tick；非空列表准备所选 K 线周期。省略时默认 1m。")

    @model_validator(mode="after")
    def validate_range(self):
        from pipeline.models import DataRequest

        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("时间须包含 UTC 或时区偏移")
        self.start, self.end = self.start.astimezone(UTC), self.end.astimezone(UTC)
        request = DataRequest((self.symbol,), self.start, self.end,
                              bar_intervals=tuple(self.intervals), market=self.product)
        self.symbol = request.symbols[0]
        self.intervals = list(request.bar_intervals)
        return self


class DataDeletePlanRequest(DataRange):
    delete_archives: bool = False


class DataJobRequest(DataDeletePlanRequest):
    action: Literal["prepare", "validate", "materialize", "export", "delete"]
    deletion_snapshot: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confirmation: str | None = None

    @model_validator(mode="after")
    def validate_deletion(self):
        if self.product == "futures_um" and self.action in {"prepare", "materialize"} and any(level in {"3d", "1w", "1M"} for level in self.intervals):
            raise ValueError("跨日周期请使用交易对配置的历史追加任务")
        if self.action == "materialize" and not self.intervals:
            raise ValueError("生成 K 线需要选择 K 线级别")
        if self.action == "delete":
            if self.deletion_snapshot is None or self.confirmation != f"DELETE {self.symbol}":
                raise ValueError("请先预览删除范围并输入 DELETE 与品种确认")
        elif self.delete_archives or self.deletion_snapshot is not None or self.confirmation is not None:
            raise ValueError("删除确认参数只能用于删除任务")
        return self


class DataMarket(BaseModel):
    symbol: str
    base: str
    quote: str
    product: Literal["futures_um", "options"] = "futures_um"
    underlying: str | None = None
    expiry: int | None = None
    strike: str | None = None
    right: Literal["CALL", "PUT"] | None = None
    multiplier: str | None = None


class DataCapabilities(BaseModel):
    market: str
    symbols: list[str]
    intervals: list[str]
    can_operate: bool
    max_range_days: int | None = None
    max_preview_minutes: int = 15
    realtime_ticks: bool = True
    realtime_base_levels: list[str] = Field(default_factory=lambda: ["tick", "1s", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"])
    realtime_products: list[str] = Field(default_factory=lambda: ["futures_um"])
    products: list[str] = Field(default_factory=lambda: ["futures_um", "options"])
    option_intervals: list[str] = Field(default_factory=lambda: ["1m", "5m", "15m"])
    option_historical_ticks: bool = False
    option_recent_limit: int = 500


class DataDeletionPlan(BaseModel):
    product: Literal["futures_um", "options"] = "futures_um"
    snapshot: str
    file_count: int
    total_bytes: int
    groups: dict[str, dict[str, int]]
    files: list[dict]
    symbol: str
    start: str
    end: str
    delete_archives: bool
    message: str


class DataJobResponse(BaseModel):
    id: str
    action: str
    status: str
    created_at: str
    finished_at: str | None
    parameters: dict
    error: str | None
    result: dict | None
    progress: str = ""
    log: str = ""
    log_truncated: bool = False
    cancellable: bool = False


class DataPolicy(BaseModel):
    """Desired service parameters; runtime progress belongs to files."""
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=30)
    product: Literal["futures_um", "options"] = "futures_um"
    base_level: Literal["auto", "tick", "1s", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"] = "1m"
    intervals: list[str] = Field(default_factory=list, max_length=16)
    realtime_enabled: bool = True
    history_enabled: bool = False
    history_mode: Literal["all", "start", "duration"] = "all"
    history_start: datetime | None = None
    history_days: int = Field(default=30, ge=1, le=36500)

    @model_validator(mode="after")
    def validate_policy(self):
        from core.market.instruments import BINANCE_KLINE_INTERVALS, INTERVALS, normalize_symbol

        self.symbol = normalize_symbol(self.symbol, self.product)
        order = ["tick", *INTERVALS]
        self.intervals = list(dict.fromkeys(self.intervals))
        if any(value not in INTERVALS for value in self.intervals):
            raise ValueError("请选择 Binance 支持的 K 线周期")
        if self.product == "futures_um" and self.base_level != "auto":
            if self.base_level == "1s":
                self.base_level = "1m"
            self.intervals = list(BINANCE_KLINE_INTERVALS)
        elif self.base_level != "auto" and any(order.index(value) < order.index(self.base_level) for value in self.intervals):
            raise ValueError("生成周期不能小于最小保存粒度")
        if self.history_start is not None:
            if self.history_start.tzinfo is None:
                raise ValueError("历史起点须包含时区")
            self.history_start = self.history_start.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        if (self.history_enabled and self.history_mode == "start"
                and (self.history_start is None or self.history_start >= datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0))):
            raise ValueError("请选择早于今天的 UTC 历史起点")
        if self.base_level == "auto" and self.realtime_enabled and any(level not in {"1m", "5m", "15m"} for level in self.intervals):
            raise ValueError("旧自动来源仅支持分钟实时；请使用 Tick 或全部官方周期配置")
        if self.product == "options":
            if self.base_level not in {"auto", "tick", "1m", "5m", "15m"} or any(value not in {"1m", "5m", "15m"} for value in self.intervals):
                raise ValueError("期权当前仅支持 Tick、1m、5m、15m")
            if self.realtime_enabled:
                raise ValueError("期权持续采集尚未接入")
            if self.history_enabled and self.base_level == "tick":
                raise ValueError("期权仅提供近期逐笔，不能追加完整历史 Tick")
        return self


class PairHistoryJob(DataPolicy):
    action: Literal["backfill"] = "backfill"


class DataPolicySave(DataPolicy):
    deletion_snapshot: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confirmation: str | None = None

    @model_validator(mode="after")
    def validate_trim_confirmation(self):
        if (self.deletion_snapshot is None) != (self.confirmation is None):
            raise ValueError("请先预览配置删除范围并确认")
        if self.confirmation is not None and self.confirmation != f"DELETE {self.symbol}":
            raise ValueError("请输入 DELETE 与交易对确认裁剪历史数据")
        return self


class PairDeletePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=30)
    product: Literal["futures_um", "options"] = "futures_um"

    @model_validator(mode="after")
    def validate_symbol(self):
        from core.market.instruments import normalize_symbol
        self.symbol = normalize_symbol(self.symbol, self.product)
        return self


class PairDeleteRequest(PairDeletePlanRequest):
    action: Literal["delete_pair"] = "delete_pair"
    deletion_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: str

    @model_validator(mode="after")
    def validate_confirmation(self):
        if self.confirmation != f"DELETE {self.symbol}":
            raise ValueError("请输入 DELETE 与交易对确认删除全部行情数据")
        return self


class PolicyTrimRequest(PairDeleteRequest):
    action: Literal["trim"] = "trim"
    policy: DataPolicy
