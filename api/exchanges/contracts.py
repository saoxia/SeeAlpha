from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExchangeAccountCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    exchange: str = Field(pattern=r"^binance$")
    account_name: str = Field(min_length=1, max_length=100)
    environment: str = Field(pattern=r"^(production|sandbox)$")
    api_key: str = Field(min_length=1, max_length=2048, repr=False)
    secret: str = Field(min_length=1, max_length=2048, repr=False)
    passphrase: str | None = Field(default=None, max_length=2048, repr=False)


class ExchangeAccountUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    account_name: str = Field(min_length=1, max_length=100)
    environment: str = Field(pattern=r"^(production|sandbox)$")
    api_key: str | None = Field(default=None, max_length=2048, repr=False)
    secret: str | None = Field(default=None, max_length=2048, repr=False)
    passphrase: str | None = Field(default=None, max_length=2048, repr=False)


class ExchangeAccountResponse(BaseModel):
    id: int
    exchange: str
    account_name: str
    environment: str
    key_hint: str
    market_symbols: list[str] = Field(
        default_factory=list, deprecated=True,
        description="Historical compatibility metadata only; not configurable or used to filter accounts.",
    )
    last_test_status: str | None = None
    last_tested_at: int | None = None
    created_at: int
    updated_at: int


class ExchangeAccountNavigationResponse(BaseModel):
    id: int
    exchange: str
    account_name: str


class EquityCandleResponse(BaseModel):
    bucket_start: int
    open: str
    high: str
    low: str
    close: str
    sample_count: int


class EquityCandleSeriesResponse(BaseModel):
    account_id: int | None = None
    account_name: str
    currency: str
    metric: str = Field(default="total_equity", pattern=r"^total_equity$")
    interval_seconds: int = 300
    is_sample: bool = False
    status: Literal["pending", "ready", "gap", "unavailable"] = "pending"
    gaps: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    candles: list[EquityCandleResponse] = Field(default_factory=list)


class ExchangeAccountTestResponse(BaseModel):
    ok: bool
    message: str


class ExchangeMarketResponse(BaseModel):
    symbol: str
    market_type: str
    base: str
    quote: str
    expiry: int | None = None


class ExchangeBalanceItemResponse(BaseModel):
    account: ExchangeAccountResponse
    balances: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class ExchangeBalancesResponse(BaseModel):
    checked_at: int
    items: list[ExchangeBalanceItemResponse] = Field(default_factory=list)


class ExchangeAccountSummaryResponse(BaseModel):
    """Display values; equity is wallet plus PnL, with source on the portfolio."""

    currency: str
    margin_ratio: str | None = None
    maintenance_margin: str | None = None
    total_equity: str | None = Field(
        default=None, description="Wallet balance plus unrealized PnL; see portfolio valuation_source."
    )
    position_value: str | None = None
    effective_leverage: str | None = None
    wallet_balance: str | None = Field(
        default=None, description="Wallet value excluding open-position unrealized PnL."
    )
    unrealized_pnl: str | None = Field(
        default=None, description="Current unrealized PnL of all open positions."
    )


class ExchangeTradeResponse(BaseModel):
    id: str
    account_id: int
    account_name: str
    exchange: str
    timestamp: int | None = None
    symbol: str
    side: str
    amount: str | None = None
    price: str | None = None
    cost: str | None = None
    fee_cost: str | None = None
    fee_currency: str = ""
    event_type: str = "fill"
    order_id: str | None = None
    order_type: str = ""
    filled: str | None = None
    status: str = ""
    source: str = "reconcile"


class ExchangePositionResponse(BaseModel):
    id: str
    account_id: int
    account_name: str
    exchange: str
    symbol: str
    side: str
    contracts: str | None = None
    contract_size: str | None = None
    entry_price: str | None = None
    mark_price: str | None = None
    notional: str | None = None
    leverage: str | None = None
    unrealized_pnl: str | None = None
    maintenance_margin: str | None = None
    liquidation_price: str | None = None
    margin_mode: str = ""


class ExchangeAssetResponse(BaseModel):
    account_id: int
    account_name: str
    currency: str
    balance: str
    price_usdt: str | None = None
    value_usdt: str | None = None
    priced_at: int | None = None
    small: bool = False
    stale: bool = False
    error: str | None = None


class ExchangePortfolioResponse(BaseModel):
    checked_at: int
    account: ExchangeAccountSummaryResponse
    assets: list[ExchangeAssetResponse] = Field(default_factory=list)
    valuation_source: Literal["exchange", "estimate"] = "exchange"
    trades: list[ExchangeTradeResponse] = Field(default_factory=list)
    positions: list[ExchangePositionResponse] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
    warnings: dict[str, str] = Field(default_factory=dict)
