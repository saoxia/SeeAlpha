"""Account configuration and encrypted credentials; no exchange network access."""

import json
import re
import sqlite3

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from ..db.errors import StorageConflict
from ..db.exchange_repository import exchange_repository


class Accounts:
    def __init__(self, database, settings):
        self.repository = exchange_repository(database)
        self.database, self.settings = database, settings

    def cipher(self):
        key = self.settings.credential_key.encode()
        path = self.settings.credential_key_path or self.database.path.parent / "credential_key"
        if not key:
            if path.exists():
                key = path.read_bytes().strip()
            else:
                if self.repository.list_exchange_accounts():
                    raise HTTPException(
                        503, "缺少已有账户的凭据加密密钥，请配置原 SYSTEM_CREDENTIAL_KEY。"
                    )
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    with path.open("xb") as file:
                        path.chmod(0o600)
                        file.write(Fernet.generate_key())
                except FileExistsError:
                    pass
                key = path.read_bytes().strip()
        try:
            return Fernet(key)
        except ValueError:
            raise HTTPException(503, "凭据加密密钥格式无效。") from None

    def decrypt(self, row):
        try:
            result = json.loads(self.cipher().decrypt(row["credentials_encrypted"].encode()))
        except (InvalidToken, ValueError, UnicodeError):
            raise HTTPException(503, "已有账户凭据无法解密，请检查原加密密钥。") from None
        if result.get("version") != 1:
            raise HTTPException(503, "不支持的凭据存储版本。")
        return result

    def row(self, account_id):
        row = self.repository.get_exchange_account(account_id)
        if row is None:
            raise HTTPException(404, "账户不存在。")
        return row

    def public(self, row):
        key = self.decrypt(row).get("apiKey", "")
        return {
            **{
                name: row[name]
                for name in (
                    "id",
                    "exchange",
                    "account_name",
                    "environment",
                    "last_test_status",
                    "last_tested_at",
                    "created_at",
                    "updated_at",
                )
            },
            "key_hint": "••••" + key[-4:],
            "market_symbols": json.loads(row["market_symbols_json"]),
        }

    def list(self):
        return [self.public(row) for row in self.repository.list_exchange_accounts()]

    def navigation(self):
        return [
            {name: row[name] for name in ("id", "exchange", "account_name")}
            for row in self.repository.list_exchange_accounts()
        ]

    def private(self, account_id):
        row = self.row(account_id)
        if row["exchange"] != "binance":
            raise HTTPException(422, "当前原生账户查询仅支持 Binance USD-M。")
        return {**dict(row), "credentials": self.decrypt(row)}

    def save(self, payload, account_id=None):
        old = self.row(account_id) if account_id is not None else None
        exchange = old["exchange"] if old else payload.exchange
        if exchange != "binance":
            raise HTTPException(422, "当前原生账户接入仅支持 Binance USD-M。")
        symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in payload.market_symbols))
        if any(not re.fullmatch(r"[A-Z0-9]+/USDT:USDT", s) for s in symbols):
            raise HTTPException(422, "请选择 Binance USD-M 永续交易对，例如 BTC/USDT:USDT。")
        supplied = bool(payload.api_key or payload.secret)
        if old and old["environment"] != payload.environment and not supplied:
            raise HTTPException(422, "切换环境时需要重新填写完整凭据。")
        if not old or supplied:
            if not payload.api_key or not payload.secret:
                raise HTTPException(422, "必须同时填写 API Key 和 Secret。")
            encrypted = (
                self.cipher()
                .encrypt(
                    json.dumps(
                        {
                            "version": 1,
                            "apiKey": payload.api_key,
                            "secret": payload.secret,
                        }
                    ).encode()
                )
                .decode()
            )
        else:
            encrypted = old["credentials_encrypted"]
        values = {
            "account_name": payload.account_name,
            "environment": payload.environment,
            "credentials_encrypted": encrypted,
            "market_symbols_json": json.dumps(symbols),
        }
        try:
            if old:
                row = self.repository.update_exchange_account(account_id, **values)
            else:
                row = self.repository.create_exchange_account(exchange=exchange, **values)
        except (sqlite3.IntegrityError, StorageConflict):
            raise HTTPException(409, "同一交易所内账户名称不能重复。") from None
        return self.public(row)
