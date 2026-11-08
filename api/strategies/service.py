"""Authorize a package version, then issue it to the engine for one tenant."""

from fastapi import HTTPException

from api.db.errors import StorageConflict
from engine.runtime import EngineConflict
from engine.strategies.registry import ParameterError, require_package, validate

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
                versions.append({**version, "grants": grants})
            packages.append({**package, "versions": versions})
        return packages

    def create_package(self, code_key, name, description):
        try:
            require_package(code_key)
        except ParameterError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return self.repository.create_package(code_key, name.strip(), description)
        except StorageConflict as exc:
            raise HTTPException(409, "策略包名称或实现不能重复。") from exc

    def create_version(self, package_id, version, parameters):
        package = self.repository.get_package(package_id)
        if package is None:
            raise HTTPException(404, "策略包不存在。")
        try:
            stored = validate(package["code_key"], parameters or {})
        except ParameterError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return self.repository.create_version(package["id"], version, stored)
        except StorageConflict as exc:
            raise HTTPException(409, "同一策略包的版本号不能重复。") from exc

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
        merged = {**version["parameters"], **(overrides or {})}
        try:
            parameters = validate(package["code_key"], merged)
        except ParameterError as exc:
            raise HTTPException(422, str(exc)) from exc
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
            })
        except EngineConflict as exc:
            self.repository.delete_issuance(issuance["id"])
            raise HTTPException(409, exc.message) from exc
        return {
            **issuance,
            "code_key": package["code_key"],
            "version": version["version"],
            "status": "loaded",
        }

    def _require_account(self, tenant, account_id):
        if tenant["purpose"] == "development":
            rows = self.tenants.repository.list_virtual_accounts(tenant["id"])
        else:
            rows = self.tenants.accounts.repository.list_exchange_accounts(tenant["id"])
        if not any(str(row["id"]) == account_id for row in rows):
            raise HTTPException(404, "账户不属于该租户。")
