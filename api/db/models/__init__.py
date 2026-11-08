"""Portable ORM mappings for API-managed business records."""

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


ID = BigInteger().with_variant(Integer(), "sqlite")
DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    __table_args__ = (Index("orm_users_username_unique", func.lower(username), unique=True),)


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class LoginFailure(Base):
    __tablename__ = "login_failures"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    identity_hash: Mapped[str] = mapped_column(Text, nullable=False)
    attempted_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    __table_args__ = (Index("orm_login_failures_lookup", "identity_hash", "attempted_at"),)


class TenantTemplate(Base):
    """Empty creation preset. It is not a runnable tenant and holds no market or account data."""

    __tablename__ = "tenant_templates"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    __table_args__ = (
        CheckConstraint("purpose in ('production','development')", name="tenant_templates_purpose_check"),
        Index("tenant_templates_name_unique", func.lower(name), unique=True),
    )


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(ID, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active", server_default="active")
    system_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    archived_at: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (
        CheckConstraint("purpose in ('production','development')", name="tenants_purpose_check"),
        CheckConstraint("status in ('active','archived')", name="tenants_status_check"),
        Index("tenants_name_unique", func.lower(name), unique=True),
        Index("tenants_system_key_unique", system_key, unique=True),
    )


class VirtualAccount(Base):
    __tablename__ = "virtual_accounts"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ID, nullable=False, index=True)
    account_name: Mapped[str] = mapped_column(Text, nullable=False)
    initial_capital: Mapped[str] = mapped_column(Text, nullable=False)
    quote_currency: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    __table_args__ = (
        Index("virtual_accounts_name_unique", "tenant_id", func.lower(account_name), unique=True),
    )


class ExchangeAccount(Base):
    __tablename__ = "exchange_accounts"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ID, nullable=False)
    exchange: Mapped[str] = mapped_column(Text, nullable=False)
    account_name: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    market_symbols_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    last_test_status: Mapped[str | None] = mapped_column(Text)
    last_tested_at: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    __table_args__ = (
        CheckConstraint("environment in ('production','sandbox')"),
        CheckConstraint("last_test_status is null or last_test_status in ('success','failed')"),
        Index(
            "orm_account_tenant_name_unique",
            "tenant_id",
            exchange,
            func.lower(account_name),
            unique=True,
        ),
    )


class LedgerOperation(Base):
    __tablename__ = "ledger_operations"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[int] = mapped_column(ID, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    client_order_id: Mapped[str | None] = mapped_column(Text)
    order_id: Mapped[str | None] = mapped_column(Text)
    trade_id: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_id: Mapped[str | None] = mapped_column(Text)
    body: Mapped[dict] = mapped_column(DOCUMENT, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "event_type in ('place','amend','cancel','fill','reject','expire','correction','cashflow')",
            name="ledger_operations_event_type_check",
        ),
        Index("orm_ledger_time", timestamp.desc(), id.desc()),
        Index("orm_ledger_account_time", account_id, environment, timestamp.desc(), id.desc()),
        Index("orm_ledger_order", account_id, environment, exchange, symbol, order_id),
        Index("orm_ledger_client", account_id, environment, exchange, symbol, client_order_id),
        Index(
            "orm_ledger_fill",
            account_id,
            environment,
            exchange,
            symbol,
            trade_id,
            unique=True,
            sqlite_where=text("event_type = 'fill'"),
            postgresql_where=text("event_type = 'fill'"),
        ),
    )


class LedgerObservation(Base):
    __tablename__ = "ledger_observations"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        Text, ForeignKey("ledger_operations.id"), nullable=False, index=True
    )
    body: Mapped[dict] = mapped_column(DOCUMENT, nullable=False)


class LedgerAlias(Base):
    __tablename__ = "ledger_aliases"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        Text, ForeignKey("ledger_operations.id"), nullable=False, index=True
    )


class LedgerSync(Base):
    __tablename__ = "ledger_sync"
    account_id: Mapped[int] = mapped_column(ID, primary_key=True)
    environment: Mapped[str] = mapped_column(Text, primary_key=True)
    body: Mapped[dict] = mapped_column(DOCUMENT, nullable=False)


class OlapState(Base):
    __tablename__ = "olap_state"
    name: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(DOCUMENT, nullable=False)
    __table_args__ = (CheckConstraint("name in ('settings', 'monitor', 'cache')"),)


class ManagementJob(Base):
    __tablename__ = "management_jobs"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(DOCUMENT, nullable=False)


class MarketDataPolicy(Base):
    __tablename__ = "market_data_policies"
    tenant_id: Mapped[int] = mapped_column(ID, primary_key=True)
    product: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    parameters: Mapped[dict] = mapped_column(DOCUMENT, nullable=False)


class BacktestReport(Base):
    __tablename__ = "backtest_reports"
    url: Mapped[str] = mapped_column(Text, primary_key=True)
    modified_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(DOCUMENT, nullable=False)


class EquityEvent(Base):
    __tablename__ = "equity_events"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ID, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    body: Mapped[dict] = mapped_column(DOCUMENT, nullable=False)
    __table_args__ = (Index("equity_events_account_time", account_id, environment, timestamp, id),)


class EquityPoint(Base):
    __tablename__ = "equity_points"
    event_id: Mapped[int] = mapped_column(ID, ForeignKey("equity_events.id"), primary_key=True)
    body: Mapped[dict] = mapped_column(DOCUMENT, nullable=False)


class EquityState(Base):
    __tablename__ = "equity_state"
    account_id: Mapped[int] = mapped_column(ID, primary_key=True)
    environment: Mapped[str] = mapped_column(Text, primary_key=True)
    body: Mapped[dict] = mapped_column(DOCUMENT, nullable=False)


class EquityRebuild(Base):
    __tablename__ = "equity_rebuilds"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[int] = mapped_column(ID, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[dict] = mapped_column(DOCUMENT, nullable=False)
