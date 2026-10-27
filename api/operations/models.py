"""Project ledger contracts. No credentials or unrestricted raw exchange payloads."""

from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EventType = Literal["place", "amend", "cancel", "fill", "reject", "expire", "correction"]
Source = Literal["project", "external", "reconciliation"]


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=200)
    account_id: int = Field(gt=0)
    account_name: str = Field(min_length=1, max_length=100)
    exchange: str = Field(default="binance", min_length=1, max_length=30)
    environment: Literal["production", "sandbox"]
    symbol: str = Field(min_length=1, max_length=100)
    event_type: EventType
    timestamp: int = Field(gt=0, le=9_000_000_000_000_000)
    evidence: Literal["project", "venue", "reconciliation", "legacy"]
    client_order_id: str | None = Field(default=None, max_length=200)
    order_id: str | None = Field(default=None, max_length=200)
    trade_id: str | None = Field(default=None, max_length=200)
    strategy_id: str | None = Field(default=None, max_length=200)
    side: str = Field(default="", max_length=20)
    order_type: str = Field(default="", max_length=40)
    amount: str | None = None
    price: str | None = None
    cost: str | None = None
    filled: str | None = None
    fee_cost: str | None = None
    fee_currency: str = Field(default="", max_length=30)
    status: str = Field(default="", max_length=50)

    @field_validator("client_order_id", "order_id", "trade_id", "strategy_id")
    @classmethod
    def clean_id(cls, value):
        return value or None

    @field_validator("amount", "price", "cost", "filled", "fee_cost")
    @classmethod
    def finite_decimal(cls, value):
        try:
            if value is not None and (len(value) > 100 or not Decimal(value).is_finite()):
                raise ValueError("需要有限的十进制数值")
        except InvalidOperation:
            raise ValueError("需要有限的十进制数值") from None
        return value

    @model_validator(mode="after")
    def identity(self):
        if self.event_type == "fill" and not self.trade_id:
            raise ValueError("真实成交必须提供交易所成交 ID")
        if self.evidence != "reconciliation" and not (self.client_order_id or self.order_id):
            raise ValueError("操作记录必须提供订单标识")
        if self.evidence != "project" and self.strategy_id:
            raise ValueError("外部证据不能声明策略归属")
        if self.evidence == "reconciliation" and self.event_type != "correction":
            raise ValueError("对账修正不能声明为真实成交")
        return self


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: int | None = Field(default=None, gt=0)
    start_ms: int | None = Field(default=None, gt=0)
    end_ms: int | None = Field(default=None, gt=0)
