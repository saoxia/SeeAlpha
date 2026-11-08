"""Create, archive, restore and delete tenants. Templates stay empty."""

import io
import json
import os
import tarfile
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from api.db.errors import StorageConflict
from api.market_data.contracts import DataPolicy

from .repository import TenantRepository

PURPOSES = {"production", "development"}
KEY_FIELDS = ("exchange", "environment", "api_key", "secret", "passphrase")


def canonical_capital(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise HTTPException(422, "初始资金无效。") from None
    digits = amount.as_tuple()
    if not amount.is_finite() or amount <= 0 or digits.exponent < -18 or len(digits.digits) > 30:
        raise HTTPException(422, "初始资金必须是大于零的十进制数。")
    return format(amount, "f")


class TenantService:
    def __init__(self, database, engine, accounts):
        self.database = database
        self.repository = TenantRepository(database)
        self.engine = engine
        self.accounts = accounts
        self.archives = database.path.parent / "tenant-archives"

    def publish(self, tenant):
        return {
            key: tenant[key]
            for key in (
                "id", "template_id", "purpose", "name", "description", "status",
                "created_at", "archived_at",
            )
        }

    def list_templates(self):
        return self.repository.list_templates()

    def create_template(self, purpose, name, description):
        if purpose not in PURPOSES:
            raise HTTPException(422, "模板类型只能是正式或开发。")
        try:
            return self.repository.create_template(purpose, name.strip(), description)
        except StorageConflict as exc:
            raise HTTPException(409, "模板名称不能重复。") from exc

    def list_tenants(self):
        return [self.publish(tenant) for tenant in self.repository.list_tenants()]

    def require_tenant(self, tenant_id):
        tenant = self.repository.get_tenant(tenant_id)
        if tenant is None:
            raise HTTPException(404, "租户不存在。")
        return tenant

    def require_active(self, tenant_id):
        tenant = self.require_tenant(tenant_id)
        if tenant["status"] != "active":
            raise HTTPException(409, "已归档的租户不能交易、回测或修改内容。")
        return tenant

    def create_tenant(self, template_id, name, description):
        template = self.repository.get_template(template_id)
        if template is None:
            raise HTTPException(404, "模板不存在。")
        try:
            tenant = self.repository.create_tenant(
                template["id"], template["purpose"], name.strip(), description
            )
        except StorageConflict as exc:
            raise HTTPException(409, "租户名称不能重复。") from exc
        self.engine.ensure_tenant(tenant["id"], tenant["purpose"], "active")
        return self.publish(tenant)

    def _archive_path(self, tenant_id):
        return self.archives / f"{tenant_id}.tar.gz"

    def _business_slice(self, tenant):
        from api.market_data.repository import PolicyRepository

        policies = []
        if self.database.backend == "orm":
            policies = PolicyRepository(self.database).records(tenant["id"])
        accounts = self.accounts.repository.list_exchange_accounts(tenant["id"])
        return {
            "tenant": self.publish(tenant),
            "policies": policies,
            "exchange_accounts": [dict(row) for row in accounts],
            "virtual_accounts": self.repository.list_virtual_accounts(tenant["id"]),
            "related": self.repository.related_records(tenant["id"]),
        }

    def _write_archive(self, tenant_id, payload):
        self.archives.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self._archive_path(tenant_id)
        temporary = target.with_suffix(f".{uuid4().hex}.tmp")
        raw = json.dumps(payload, ensure_ascii=False).encode()
        try:
            with tarfile.open(temporary, "w:gz") as archive:
                info = tarfile.TarInfo("tenant.json")
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def _read_archive(self, tenant_id):
        path = self._archive_path(tenant_id)
        if not path.is_file():
            raise HTTPException(409, "找不到该租户的归档文件。")
        with tarfile.open(path, "r:gz") as archive:
            member = archive.extractfile("tenant.json")
            if member is None:
                raise HTTPException(409, "归档内容不完整。")
            return json.loads(member.read().decode())

    def archive(self, tenant_id):
        tenant = self.require_tenant(tenant_id)
        if tenant["status"] == "archived":
            raise HTTPException(409, "租户已经归档。归档不会删除租户。")
        payload = {
            "business": self._business_slice(tenant),
            "engine": self.engine.export_tenant(tenant["id"]),
        }
        path = self._write_archive(tenant["id"], payload)
        try:
            self.engine.drop_tenant(tenant["id"], remove=False)
            stored = self.repository.set_status(tenant["id"], "archived")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {**self.publish(stored), "archive": path.name}

    def restore(self, tenant_id):
        tenant = self.require_tenant(tenant_id)
        if tenant["status"] != "archived":
            raise HTTPException(409, "只有已归档的租户可以恢复。")
        payload = self._read_archive(tenant["id"])
        self.engine.import_tenant(tenant["id"], tenant["purpose"], payload.get("engine") or {})
        stored = self.repository.set_status(tenant["id"], "active")
        return self.publish(stored)

    def delete(self, tenant_id):
        tenant = self.require_tenant(tenant_id)
        self.engine.drop_tenant(tenant["id"], remove=True)
        self.repository.delete_records(tenant["id"])
        self._archive_path(tenant["id"]).unlink(missing_ok=True)
        return {"id": tenant["id"], "deleted": True}

    def list_accounts(self, tenant_id):
        self.require_tenant(tenant_id)
        exchange = []
        for row in self.accounts.repository.list_exchange_accounts(tenant_id):
            public = self.accounts.public(row)
            public["tenant_id"] = row["tenant_id"]
            public["kind"] = "exchange"
            exchange.append(public)
        virtual = [
            {**row, "kind": "virtual"} for row in self.repository.list_virtual_accounts(tenant_id)
        ]
        return [*exchange, *virtual]

    def add_account(self, tenant_id, payload):
        tenant = self.require_active(tenant_id)
        supplied_key = any(getattr(payload, field, None) for field in KEY_FIELDS)
        if tenant["purpose"] == "development":
            if supplied_key:
                raise HTTPException(422, "开发租户只能添加虚拟账户，不能保存交易所密钥。")
            if payload.initial_capital is None or not payload.quote_currency:
                raise HTTPException(422, "虚拟账户需要初始资金和报价币种。")
            try:
                account = self.repository.create_virtual_account(
                    tenant["id"], payload.account_name.strip(), canonical_capital(payload.initial_capital),
                    payload.quote_currency,
                )
            except StorageConflict as exc:
                raise HTTPException(409, "同一租户内虚拟账户名称不能重复。") from exc
            return {**account, "kind": "virtual"}
        if payload.initial_capital is not None or payload.quote_currency:
            raise HTTPException(422, "正式租户的初始资金属于交易所账户本身，不能写成虚拟账户。")
        from api.exchanges.contracts import ExchangeAccountCreateRequest

        try:
            exchange_payload = ExchangeAccountCreateRequest(
                exchange=payload.exchange or "binance",
                account_name=payload.account_name,
                environment=payload.environment or "production",
                api_key=payload.api_key or "",
                secret=payload.secret or "",
                passphrase=payload.passphrase,
            )
        except ValueError as exc:
            raise HTTPException(422, "正式租户需要完整的交易所账户密钥。") from exc
        created = self.accounts.save(exchange_payload, tenant_id=tenant["id"])
        created["tenant_id"] = tenant["id"]
        created["kind"] = "exchange"
        return created

    def list_subscriptions(self, tenant_id):
        self.require_tenant(tenant_id)
        if self.database.backend != "orm":
            raise HTTPException(503, "行情订阅意图需要已迁移的 ORM 数据库。")
        from api.market_data.repository import PolicyRepository

        return PolicyRepository(self.database).list(tenant_id=tenant_id)

    def save_subscription(self, tenant_id, payload):
        tenant = self.require_active(tenant_id)
        if self.database.backend != "orm":
            raise HTTPException(503, "行情订阅意图需要已迁移的 ORM 数据库。")
        from api.market_data.repository import PolicyRepository

        try:
            policy = DataPolicy.model_validate(payload.model_dump())
        except (ValidationError, ValueError) as exc:
            raise HTTPException(422, "行情订阅参数无效。") from exc
        stored = PolicyRepository(self.database).upsert(tenant["id"], policy)
        self.engine.ensure_tenant(tenant["id"], tenant["purpose"], tenant["status"])
        self.engine.note_subscription(
            tenant["id"], policy.product, policy.symbol, "intent", "configured",
            {"realtime_enabled": policy.realtime_enabled, "history_enabled": policy.history_enabled,
             "history_start": stored.get("history_start"), "base_level": policy.base_level},
        )
        return stored
