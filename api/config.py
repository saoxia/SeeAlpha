"""API configuration; runtime paths are relative to the repository, not the shell."""

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    database_path: Path
    cookie_secure: bool = True
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")
    allowed_origins: tuple[str, ...] = ()
    session_seconds: int = 7 * 24 * 60 * 60
    admin_username: str = "admin"
    credential_key: str = field(default="", repr=False)
    credential_key_path: Path | None = None
    database_url: str = field(default="", repr=False)
    database_schema: str = ""
    require_database_url: bool = False
    operation_sync_seconds: int = 0
    equity_enabled: bool = False

    @classmethod
    def from_environment(cls):
        database_url = os.getenv("DATABASE_URL", "")
        database_url_file = Path(
            os.getenv("DATABASE_URL_FILE", str(PROJECT / ".local/config/api/database_url"))
        )
        if not database_url and os.getenv("DATABASE_URL_FILE") and not database_url_file.exists():
            raise ValueError("指定的数据库连接文件不存在。")
        if not database_url and (database_url_file.exists() or database_url_file.is_symlink()):
            if database_url_file.is_symlink() or database_url_file.stat().st_mode & 0o077:
                raise ValueError("数据库连接文件不能是符号链接，权限须为 600。")
            database_url = database_url_file.read_text().strip()
            if not database_url:
                raise ValueError("数据库连接文件不能为空。")
        path = Path(os.getenv("AUTH_DB_PATH", ".local/state/api/auth.db"))
        if not path.is_absolute():
            path = PROJECT / path
        return cls(
            database_path=path,
            database_url=database_url,
            database_schema=os.getenv("DATABASE_SCHEMA", ""),
            require_database_url=True,
            equity_enabled=os.getenv("EQUITY_ENABLED", "true").lower() not in {"0", "false", "no"},
            operation_sync_seconds=max(0, int(os.getenv("OPERATION_SYNC_SECONDS", "300"))),
            cookie_secure=os.getenv("COOKIE_SECURE", "true").lower() not in {"0", "false", "no"},
            admin_username=os.getenv("UPDATE_ADMIN_USERNAME", "admin"),
            credential_key=os.getenv("SYSTEM_CREDENTIAL_KEY", "")
            or os.getenv("ETL_CREDENTIAL_KEY", ""),
            credential_key_path=Path(
                os.getenv(
                    "SYSTEM_CREDENTIAL_KEY_FILE", str(PROJECT / ".local/config/api/credential_key")
                )
            ),
            allowed_hosts=tuple(
                h.strip()
                for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
                if h.strip()
            ),
            allowed_origins=tuple(
                h.strip() for h in os.getenv("ALLOWED_ORIGINS", "").split(",") if h.strip()
            ),
        )
