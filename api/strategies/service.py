"""Authorize a package version, then issue its archive to the engine for one tenant."""

import base64

from fastapi import HTTPException

from api.db.errors import StorageConflict
from core.engine_contracts import EngineConflict
from engine.strategies.package import PackageError, digest, open_package

from .repository import StrategyRepository


class StrategyStore:
    def __init__(self, database, engine, tenants):
        self.repository = StrategyRepository(database)
        self.engine = engine
        self.tenants = tenants

    def list_packages(self):
        packages = []
        for package in self.repository.list_packages():
            versions = []
            for version in self.repository.list_versions(package["id"]):
                grants = [row["purpose"] for row in self.repository.list_grants(version["id"])]
                versions.append({**_public_version(version), "grants": grants})
            packages.append({**package, "versions": versions})
        return packages

    def create_package(self, archive_b64, parameters):
        metadata, prepared, archive = _open(archive_b64, parameters)
        try:
            package = self.repository.create_package(
                metadata.name, metadata.title, metadata.description,
            )
        except StorageConflict as exc:
            raise HTTPException(409, "策略包名称或实现不能重复。") from exc
        version = self._store_version(package, metadata, prepared, archive)
        return {**package, "version": _public_version(version)}

    def create_version(self, package_id, archive_b64, parameters):
        package = self.repository.get_package(package_id)
        if package is None:
            raise HTTPException(404, "策略包不存在。")
        metadata, prepared, archive = _open(archive_b64, parameters)
        if metadata.name != package["code_key"]:
            raise HTTPException(422, "策略包名称与归档不一致。")
        version = self._store_version(package, metadata, prepared, archive)
        return _public_version(version)

    def grant(self, version_id, purpose):
        if purpose not in {"production", "development"}:
            raise HTTPException(422, "授权类型只能是正式或开发。")
        version = self.repository.get_version(version_id)
        if version is None:
            raise HTTPException(404, "策略版本不存在。")
        try:
            return self.repository.create_grant(version["id"], purpose)
        except StorageConflict as exc:
            found = self.repository.find_grant(version["id"], purpose)
            if found is not None:
                return found
            raise HTTPException(409, "策略授权不能重复。") from exc

    def list_loads(self, tenant_id):
        self.tenants.require_tenant(tenant_id)
        loads = []
        for issuance in self.repository.list_issuances(tenant_id):
            version = self.repository.get_version(issuance["version_id"])
            package = self.repository.get_package(version["package_id"]) if version else None
            loads.append({
                **issuance,
                "code_key": None if package is None else package["code_key"],
                "version": None if version is None else version["version"],
                "entry_point": None if version is None else version["entry_point"],
                "package_hash": None if version is None else version["package_hash"],
                "status": "loaded",
            })
        return loads

    def issue(self, tenant_id, account_id, version_id, overrides):
        tenant = self.tenants.require_active(tenant_id)
        version = self.repository.get_version(version_id)
        if version is None:
            raise HTTPException(404, "策略版本不存在。")
        package = self.repository.get_package(version["package_id"])
        if package is None:
            raise HTTPException(404, "策略包不存在。")
        if self.repository.find_grant(version["id"], tenant["purpose"]) is None:
            if tenant["purpose"] == "production":
                raise HTTPException(409, "正式租户加载需要单独的正式授权。")
            raise HTTPException(409, "该策略版本还不能加载到开发租户。")
        account = str(account_id).strip()
        self._require_account(tenant, account)
        try:
            _metadata, parameters = open_package(
                version["archive"], {**version["parameters"], **(overrides or {})},
            )
        except PackageError as exc:
            raise HTTPException(422, str(exc)) from exc
        if digest(version["archive"]) != version["package_hash"]:
            raise HTTPException(409, "策略包哈希不一致。")
        issuance = self.repository.create_issuance(
            tenant["id"], account, version["id"], tenant["purpose"], parameters,
        )
        try:
            self.engine.receive_strategy(tenant["id"], {
                "account_id": account,
                "issuance_id": str(issuance["id"]),
                "code_key": package["code_key"],
                "version": version["version"],
                "grant_purpose": tenant["purpose"],
                "parameters": parameters,
                "entry_point": version["entry_point"],
                "package_hash": version["package_hash"],
                "archive": base64.b64encode(version["archive"]).decode(),
            })
        except EngineConflict as exc:
            self.repository.delete_issuance(issuance["id"])
            raise HTTPException(409, exc.message) from exc
        return {
            **issuance,
            "code_key": package["code_key"],
            "version": version["version"],
            "entry_point": version["entry_point"],
            "package_hash": version["package_hash"],
            "status": "loaded",
        }

    def _store_version(self, package, metadata, prepared, archive):
        try:
            return self.repository.create_version(
                package["id"], metadata.version, prepared, metadata.entry_point,
                metadata.digest, archive,
            )
        except StorageConflict as exc:
            raise HTTPException(409, "同一策略包的版本号不能重复。") from exc

    def backtest(self, tenant_id, account_id, start, end, interval):
        tenant = self.tenants.require_active(tenant_id)
        if tenant["purpose"] != "development":
            raise HTTPException(409, "历史回测只在开发租户上运行。")
        account = self._virtual_account(tenant, str(account_id).strip())
        try:
            self.engine.runnable(tenant["id"], "backtest")
            return self.engine.run_backtest(
                tenant["id"], account["id"], start, end, interval or "1m",
                account["initial_capital"], account["quote_currency"],
            )
        except EngineConflict as exc:
            raise HTTPException(409, exc.message) from exc

    def submit_backtest(self, tenant_id, account_id, start, end, interval, *, idempotency_key=None):
        """Submit once and return a durable job handle; no long HTTP wait."""
        tenant = self.tenants.require_active(tenant_id)
        if tenant["purpose"] != "development":
            raise HTTPException(409, "历史回测只在开发租户上运行。")
        account = self._virtual_account(tenant, str(account_id).strip())
        try:
            self.engine.runnable(tenant["id"], "backtest")
            return self.engine.submit_task(
                tenant["id"], "backtest", {
                    "account_id": str(account["id"]),
                    "start": start, "end": end, "interval": interval or "1m",
                    "starting_balance": str(account["initial_capital"]),
                    "quote_currency": account["quote_currency"],
                }, idempotency_key=idempotency_key,
            )
        except EngineConflict as exc:
            raise HTTPException(409, exc.message) from exc

    def _task_tenant(self, tenant_id):
        tenant = self.tenants.require_tenant(tenant_id)
        return tenant["id"]

    def tasks(self, tenant_id):
        return self.engine.list_tasks(self._task_tenant(tenant_id))

    def task(self, tenant_id, identity):
        return self.engine.task(self._task_tenant(tenant_id), identity)

    def task_result(self, tenant_id, identity):
        return self.engine.task_result(self._task_tenant(tenant_id), identity)

    def cancel_task(self, tenant_id, identity):
        return self.engine.cancel_task(self._task_tenant(tenant_id), identity)

    def retry_task(self, tenant_id, identity):
        return self.engine.retry_task(self._task_tenant(tenant_id), identity)

    def latest_backtest(self, tenant_id, account_id):
        tenant = self.tenants.require_tenant(tenant_id)
        self._virtual_account(tenant, str(account_id).strip())
        try:
            found = self.engine.latest_backtest(tenant["id"], str(account_id).strip())
        except EngineConflict as exc:
            status = 404 if "不存在" in exc.message else 409
            raise HTTPException(status, exc.message) from exc
        if found is None:
            raise HTTPException(404, "还没有回测结果。")
        return found

    def _virtual_account(self, tenant, account_id):
        if tenant["purpose"] != "development":
            raise HTTPException(409, "历史回测只使用开发租户的虚拟账户。")
        rows = self.tenants.repository.list_virtual_accounts(tenant["id"])
        found = next((row for row in rows if str(row["id"]) == account_id), None)
        if found is None:
            raise HTTPException(404, "账户不属于该租户。")
        return found

    def _require_account(self, tenant, account_id):
        if tenant["purpose"] == "development":
            rows = self.tenants.repository.list_virtual_accounts(tenant["id"])
        else:
            rows = self.tenants.accounts.repository.list_exchange_accounts(tenant["id"])
        if not any(str(row["id"]) == account_id for row in rows):
            raise HTTPException(404, "账户不属于该租户。")


def _open(archive_b64, parameters):
    try:
        archive = base64.b64decode(archive_b64, validate=True)
        metadata, prepared = open_package(archive, parameters or {})
    except (ValueError, PackageError) as exc:
        message = str(exc) if isinstance(exc, PackageError) and str(exc) else "策略包无效。"
        raise HTTPException(422, message) from exc
    return metadata, prepared, archive


def _public_version(version):
    return {key: value for key, value in version.items() if key != "archive"}
