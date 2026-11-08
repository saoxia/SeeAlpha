"""Verify the known pre-Alembic PostgreSQL schema before recording its baseline."""

from sqlalchemy import JSON, BigInteger, Integer, String, Text, inspect, text

from .models import Base

_BASELINE_TABLES = frozenset(
    {
        "users",
        "sessions",
        "login_failures",
        "exchange_accounts",
        "ledger_operations",
        "ledger_observations",
        "ledger_aliases",
        "ledger_sync",
        "olap_state",
    }
)


def verify_existing_postgres(connection, schema):
    inspector = inspect(connection)
    tables = set(inspector.get_table_names(schema=schema))
    required = _BASELINE_TABLES
    if not (tables & required):
        return False
    if not required.issubset(tables) or "schema_versions" not in tables:
        raise RuntimeError("检测到已有数据库，但表结构不完整；请先核对，不会自动覆盖。")
    for table in Base.metadata.sorted_tables:
        if table.name not in required:
            continue
        actual = {
            column["name"]: column for column in inspector.get_columns(table.name, schema=schema)
        }
        expected = {column.name: column for column in table.columns}
        if table.name == "exchange_accounts":
            # tenant_id is added by a later migration. Baseline databases predate it.
            expected.pop("tenant_id", None)
        if table.name == "sessions":
            # impersonate_user_id is added by a later migration. Baseline databases predate it.
            expected.pop("impersonate_user_id", None)
        if not expected.keys() <= actual.keys():
            raise RuntimeError(f"已有数据库的 {table.name} 字段不完整。")
        primary = set(
            inspector.get_pk_constraint(table.name, schema=schema).get("constrained_columns") or ()
        )
        if primary != {column.name for column in table.primary_key}:
            raise RuntimeError(f"已有数据库的 {table.name} 主键不匹配。")
        for name, column in expected.items():
            value = actual[name]
            required_type = column.type.dialect_impl(connection.dialect)
            actual_type = value["type"]
            if isinstance(required_type, BigInteger):
                compatible = isinstance(actual_type, BigInteger)
            elif isinstance(required_type, Integer):
                compatible = isinstance(actual_type, Integer)
            elif isinstance(required_type, JSON):
                compatible = isinstance(actual_type, JSON)
            elif isinstance(required_type, (Text, String)):
                compatible = isinstance(actual_type, (Text, String))
            else:
                compatible = type(required_type) is type(actual_type)
            if not compatible or (not column.nullable and value["nullable"]):
                raise RuntimeError(f"已有数据库的 {table.name}.{name} 类型或约束不匹配。")
            if (
                table.name in {"users", "login_failures", "exchange_accounts"}
                and name == "id"
                and not value.get("identity")
                and "nextval(" not in str(value.get("default") or "")
            ):
                raise RuntimeError(f"已有数据库的 {table.name}.id 未配置自动生成。")
    for table, column, parent, parent_column in (
        ("sessions", "user_id", "users", "id"),
        ("ledger_observations", "operation_id", "ledger_operations", "id"),
        ("ledger_aliases", "operation_id", "ledger_operations", "id"),
    ):
        foreign_keys = inspector.get_foreign_keys(table, schema=schema)
        if not any(
            key["constrained_columns"] == [column]
            and key["referred_table"] == parent
            and key["referred_columns"] == [parent_column]
            and key.get("referred_schema") in (None, schema)
            and (
                table != "sessions"
                or key.get("options", {}).get("ondelete", "").upper() == "CASCADE"
            )
            for key in foreign_keys
        ):
            raise RuntimeError(f"已有数据库的 {table}.{column} 外键不匹配。")
    indexes = connection.execute(
        text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname=:schema"),
        {"schema": schema},
    ).all()
    definitions = [definition.lower() for _, definition in indexes]
    checks = (
        ("users", ("lower(username)",), None),
        ("exchange_accounts", ("exchange", "lower(account_name)"), None),
        (
            "ledger_operations",
            ("account_id", "environment", "exchange", "symbol", "trade_id"),
            "event_type",
        ),
    )

    def matches(value, table, expressions, predicate):
        definition, _, where = value.partition(" where ")
        keys = "(" + ", ".join(expressions) + ")"
        if (
            "unique index" not in definition
            or f".{table} " not in definition
            or keys not in definition
        ):
            return False
        if predicate is None:
            return not where
        return (
            where.replace("(", "").replace(")", "").replace("::text", "").strip()
            == "event_type = 'fill'"
        )

    for table, expressions, predicate in checks:
        if table == "exchange_accounts":
            shapes = (expressions, ("tenant_id", "exchange", "lower(account_name)"))
            found = any(
                matches(value, table, shape, predicate) for value in definitions for shape in shapes
            )
        else:
            found = any(matches(value, table, expressions, predicate) for value in definitions)
        if not found:
            raise RuntimeError(f"已有数据库的 {table} 唯一索引不匹配。")
    return True
