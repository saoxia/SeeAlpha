from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TIME_COLUMN_PRIORITY = (
    "timestamp",
    "event_time",
    "datetime",
    "time",
    "ts",
    "created_at",
    "updated_at",
)
MONITOR_REFRESH_SECONDS = 60
_REFRESH_LOCK = threading.Lock()


class ClickhouseConfigurationError(RuntimeError):
    pass


class ClickhouseConnectionError(RuntimeError):
    pass


class ClickhouseMonitor:
    def __init__(self, repository, accounts):
        self.repository = repository
        self.accounts = accounts

    def cipher(self):
        settings = self.accounts.settings
        path = settings.credential_key_path or self.accounts.database.path.parent / "credential_key"
        if (
            self.repository.get_olap_settings()
            and not settings.credential_key
            and not path.exists()
        ):
            raise ClickhouseConfigurationError("缺少已有 OLAP 配置的加密密钥。")
        return self.accounts.cipher()

    def encrypt_password(self, password: str) -> str:
        try:
            return self.cipher().encrypt(password.encode("utf-8")).decode("ascii")
        except Exception as exc:
            raise ClickhouseConfigurationError(str(exc)) from exc

    def decrypt_password(self, encrypted: str) -> str:
        try:
            return self.cipher().decrypt(encrypted.encode("ascii")).decode("utf-8")
        except Exception as exc:
            raise ClickhouseConfigurationError("已保存的 OLAP 密码无法解密。") from exc

    def public_olap_settings(self) -> dict[str, object]:
        row = self.repository.get_olap_settings()
        if row is None:
            return {
                "address": "",
                "port": 8123,
                "database": "default",
                "username": "",
                "password_configured": False,
                "updated_at": None,
            }
        return {
            "address": row["address"],
            "port": row["port"],
            "database": row["database_name"],
            "username": row["username"],
            "password_configured": True,
            "updated_at": row["updated_at"],
        }

    def save_olap_settings(
        self, *, address: str, port: int, database_name: str, username: str, password: str | None
    ) -> dict[str, object]:
        self.validate_connection_fields(address, database_name, username)
        current = self.repository.get_olap_settings()
        if password:
            password_encrypted = self.encrypt_password(password)
        elif current is not None:
            password_encrypted = current["password_encrypted"]
        else:
            raise ClickhouseConfigurationError("首次配置时必须填写 OLAP 密码。")
        self.repository.save_olap_settings(
            address=address,
            port=port,
            database_name=database_name,
            username=username,
            password_encrypted=password_encrypted,
        )
        return self.public_olap_settings()

    def validate_connection_fields(self, address: str, database_name: str, username: str) -> None:
        parsed = urllib.parse.urlsplit(address if "://" in address else f"http://{address}")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ClickhouseConfigurationError("OLAP 连接地址必须是有效的 HTTP 或 HTTPS 地址。")
        if not IDENTIFIER.fullmatch(database_name):
            raise ClickhouseConfigurationError("ClickHouse 数据库名称格式无效。")
        if not username or len(username) > 128:
            raise ClickhouseConfigurationError("ClickHouse 用户名格式无效。")

    def private_olap_settings(self) -> dict[str, Any]:
        row = self.repository.get_olap_settings()
        if row is None:
            raise ClickhouseConfigurationError("请先在系统设置中保存 OLAP 连接。")
        return {
            "address": row["address"],
            "port": int(row["port"]),
            "database": row["database_name"],
            "username": row["username"],
            "password": self.decrypt_password(row["password_encrypted"]),
        }

    def clickhouse_url(self, settings: dict[str, Any]) -> str:
        raw = settings["address"]
        parsed = urllib.parse.urlsplit(raw if "://" in raw else f"http://{raw}")
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = f"{host}:{settings['port']}"
        query = urllib.parse.urlencode(
            {"database": settings["database"], "session_timezone": "UTC"}
        )
        return urllib.parse.urlunsplit((parsed.scheme, netloc, "/", query, ""))

    def execute_json(
        self, query: str, settings: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        connection = settings or self.private_olap_settings()
        request = urllib.request.Request(
            self.clickhouse_url(connection),
            data=f"{query.rstrip()} FORMAT JSON".encode(),
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "text/plain; charset=utf-8",
                "X-ClickHouse-User": connection["username"],
                "X-ClickHouse-Key": connection["password"],
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ClickhouseConnectionError(f"ClickHouse 返回 HTTP {exc.code}。") from exc
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ClickhouseConnectionError("无法连接 ClickHouse，请检查 OLAP 配置。") from exc
        data = payload.get("data")
        if not isinstance(data, list):
            raise ClickhouseConnectionError("ClickHouse 返回了无法识别的数据。")
        return data

    def quote_literal(self, value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

    def quote_identifier(self, value: str) -> str:
        if not IDENTIFIER.fullmatch(value):
            raise ClickhouseConfigurationError(f"ClickHouse 标识符格式无效：{value[:50]}")
        return f"`{value}`"

    def available_tables(self) -> list[dict[str, str | None]]:
        settings = self.private_olap_settings()
        database_name = settings["database"]
        table_rows = self.execute_json(
            "SELECT name FROM system.tables "
            f"WHERE database = {self.quote_literal(database_name)} AND is_temporary = 0 ORDER BY name",
            settings,
        )
        column_rows = self.execute_json(
            "SELECT table, name, type, position FROM system.columns "
            f"WHERE database = {self.quote_literal(database_name)} "
            "AND (type LIKE 'DateTime%' OR type LIKE 'Nullable(DateTime%') "
            "ORDER BY table, position",
            settings,
        )
        columns: dict[str, list[str]] = {}
        for row in column_rows:
            columns.setdefault(str(row["table"]), []).append(str(row["name"]))
        return [
            {
                "name": str(row["name"]),
                "time_column": self.choose_time_column(columns.get(str(row["name"]), [])),
            }
            for row in table_rows
            if IDENTIFIER.fullmatch(str(row.get("name", "")))
        ]

    def choose_time_column(self, columns: list[str]) -> str | None:
        by_casefold = {column.casefold(): column for column in columns}
        for preferred in TIME_COLUMN_PRIORITY:
            if preferred in by_casefold:
                return by_casefold[preferred]
        return columns[0] if columns else None

    def monitor_settings(self) -> dict[str, object]:
        row = self.repository.get_clickhouse_monitor_settings()
        if row is None:
            return {
                "refresh_interval_seconds": MONITOR_REFRESH_SECONDS,
                "tables": [],
                "updated_at": None,
            }
        try:
            tables = json.loads(row["tables_json"])
        except json.JSONDecodeError:
            tables = []
        return {
            "refresh_interval_seconds": MONITOR_REFRESH_SECONDS,
            "tables": tables if isinstance(tables, list) else [],
            "updated_at": row["updated_at"],
        }

    def save_monitor_settings(
        self, *, refresh_interval_seconds: int, tables: list[str]
    ) -> dict[str, object]:
        unique_tables = list(dict.fromkeys(tables))
        if any(not IDENTIFIER.fullmatch(table) for table in unique_tables):
            raise ClickhouseConfigurationError("监控表名称格式无效。")
        self.repository.save_clickhouse_monitor_settings(
            refresh_interval_seconds=MONITOR_REFRESH_SECONDS,
            tables_json=json.dumps(unique_tables, ensure_ascii=False),
        )
        self.repository.delete_clickhouse_monitor_cache()
        return self.monitor_settings()

    def add_monitored_table(self, table: str) -> dict[str, object]:
        if not IDENTIFIER.fullmatch(table):
            raise ClickhouseConfigurationError("监控表名称格式无效。")
        configuration = self.monitor_settings()
        tables = [str(item) for item in configuration["tables"]]
        if table not in tables:
            tables.append(table)
        return self.save_monitor_settings(
            refresh_interval_seconds=MONITOR_REFRESH_SECONDS,
            tables=tables,
        )

    def remove_monitored_table(self, table: str) -> dict[str, object]:
        if not IDENTIFIER.fullmatch(table):
            raise ClickhouseConfigurationError("监控表名称格式无效。")
        configuration = self.monitor_settings()
        tables = [str(item) for item in configuration["tables"] if str(item) != table]
        return self.save_monitor_settings(
            refresh_interval_seconds=MONITOR_REFRESH_SECONDS,
            tables=tables,
        )

    def cached_freshness_status(self) -> dict[str, object]:
        cached = self.repository.get_clickhouse_monitor_cache()
        if cached is not None:
            try:
                payload = json.loads(cached["payload_json"])
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                pass
        configuration = self.monitor_settings()
        return {**configuration, "checked_at": None, "items": []}

    def refresh_freshness_cache(self) -> dict[str, object]:
        with _REFRESH_LOCK:
            try:
                result = self.freshness_status()
            except (ClickhouseConfigurationError, ClickhouseConnectionError) as exc:
                configuration = self.monitor_settings()
                checked_at = int(time.time())
                result = {
                    **configuration,
                    "checked_at": checked_at,
                    "items": [
                        self.error_item(str(table), checked_at, str(exc))
                        for table in configuration["tables"]
                    ],
                }
            checked_at = int(result["checked_at"] or time.time())
            self.repository.save_clickhouse_monitor_cache(
                payload_json=json.dumps(result, ensure_ascii=False),
                checked_at=checked_at,
            )
            return result

    def freshness_status(self) -> dict[str, object]:
        configuration = self.monitor_settings()
        selected = {str(table) for table in configuration["tables"]}
        checked_at = int(time.time())
        if not selected:
            return {**configuration, "checked_at": checked_at, "items": []}

        settings = self.private_olap_settings()
        database_name = settings["database"]
        definitions = {table["name"]: table["time_column"] for table in self.available_tables()}
        items: list[dict[str, object]] = []
        for table_name in sorted(selected):
            time_column = definitions.get(table_name)
            if table_name not in definitions:
                items.append(self.error_item(table_name, checked_at, "表不存在或无法读取。"))
                continue
            if not time_column:
                items.append(self.error_item(table_name, checked_at, "未找到 DateTime 时间字段。"))
                continue
            try:
                rows = self.execute_json(
                    "SELECT toUnixTimestamp(max("
                    f"{self.quote_identifier(str(time_column))})) AS latest_epoch FROM "
                    f"{self.quote_identifier(database_name)}.{self.quote_identifier(table_name)}",
                    settings,
                )
                raw_latest = rows[0].get("latest_epoch") if rows else None
                latest_epoch = int(raw_latest) if raw_latest not in {None, ""} else None
                lag_seconds = max(0, checked_at - latest_epoch) if latest_epoch else None
                items.append(
                    {
                        "table": table_name,
                        "time_column": time_column,
                        "latest_epoch": latest_epoch,
                        "lag_seconds": lag_seconds,
                        "healthy": lag_seconds is not None and lag_seconds <= 60,
                        "message": None if latest_epoch else "表中暂无时间数据。",
                    }
                )
            except (ClickhouseConnectionError, ClickhouseConfigurationError) as exc:
                items.append(self.error_item(table_name, checked_at, str(exc)))
        return {**configuration, "checked_at": checked_at, "items": items}

    def error_item(self, table_name: str, checked_at: int, message: str) -> dict[str, object]:
        return {
            "table": table_name,
            "time_column": None,
            "latest_epoch": None,
            "lag_seconds": None,
            "healthy": False,
            "message": message[:300],
        }
