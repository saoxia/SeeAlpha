"""Authorize a package version, then issue its archive to the engine for one tenant."""

import base64
import binascii
import hashlib
import io
import json
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from api.config import PROJECT
from api.db.auth_repository import auth_repository
from api.db.errors import StorageConflict
from core.engine_contracts import EngineConflict, QueryError

from .repository import StrategyRepository

# These built-in cross-product Demos have only *research* runtimes.
# Even an admin-created grant must not silently turn them into live strategies.
_RESEARCH_ONLY_SYSTEM_BASKETS = frozenset({
    "demo_funding_carry", "demo_option_hedged_bollinger",
})


class StrategyStore:
    def __init__(self, database, engine, tenants):
        self.repository = StrategyRepository(database)
        self.engine = engine
        self.tenants = tenants

    def seed_system_demos(self):
        """Install checked-in examples once per database; no Engine or Docker required."""
        directory = PROJECT / "strategy"
        for folder in sorted(directory.glob("demo_*")):
            manifest_path = folder / "seealpha-package.json"
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("name") != folder.name or manifest.get("format") != 1:
                raise ValueError("演示策略元数据与目录不一致。")
            existing = next((
                row for row in self.repository.list_packages(is_admin=True)
                if row["code_key"] == folder.name and row["owner_id"] is None
            ), None)
            source_files = sorted(p for p in folder.iterdir() if p.name == "seealpha-package.json"
                                  or p.suffix == ".py")
            with io.BytesIO() as memory:
                with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                    for file in source_files:
                        bundle.write(file, arcname=file.name)
                archive = memory.getvalue()
            digest = hashlib.sha256(archive).hexdigest()
            if existing is not None:
                # Grant only the immutable, checked-in research Demo version.
                # Do not authorize an unrelated or previously replaced package.
                if folder.name in {"demo_funding_carry", "demo_option_hedged_bollinger"}:
                    for version in self.repository.list_versions(existing["id"]):
                        if (
                            version["version"] != manifest["version"]
                            or version["entry_point"] != manifest["entry_point"]
                        ):
                            continue
                        old_archive = version["archive"]
                        if (
                            not isinstance(old_archive, bytes)
                            or hashlib.sha256(old_archive).hexdigest() != version["package_hash"]
                        ):
                            continue
                        try:
                            with zipfile.ZipFile(io.BytesIO(old_archive)) as saved:
                                same_files = set(saved.namelist()) == {
                                    source.name for source in source_files
                                } and all(
                                    saved.read(source.name) == source.read_bytes()
                                    for source in source_files
                                )
                        except (OSError, KeyError, zipfile.BadZipFile):
                            continue
                        if same_files:
                            self.repository.create_grant(version["id"], "development")
                continue
            try:
                package = self.repository.create_package(
                    manifest["name"], manifest["title"], manifest.get("description"),
                    owner_id=None, visibility="system",
                )
            except StorageConflict:
                # Parallel Platform startup may have seeded the same immutable code key.
                continue
            version = self.repository.create_version(
                package["id"], manifest["version"], manifest.get("parameters") or {},
                manifest["entry_point"], digest, archive,
            )
            # Only explicitly audited system demos get development grants.
            # Cross-product demos execute via restricted research endpoints;
            # their standard Strategy.on_start remains fail-closed.
            if manifest["name"] in {
                "demo_ema_crossover", "demo_multi_signal_portfolio",
                "demo_funding_carry", "demo_option_hedged_bollinger",
            }:
                self.repository.create_grant(version["id"], "development")

    def list_packages(self, viewer_id=None, is_admin=False):
        packages = []
        for package in self.repository.list_packages(viewer_id, is_admin=is_admin):
            versions = []
            for version in self.repository.list_versions(package["id"]):
                grants = [row["purpose"] for row in self.repository.list_grants(version["id"])]
                versions.append({**_public_version(version), "grants": grants})
            packages.append({**package, "versions": versions})
        return packages

    def _visible_package(self, package_id, user_id, *, is_admin=False):
        package = self.repository.get_package(package_id)
        if package is None or not (
            is_admin
            or package["visibility"] in {"system", "public"}
            or (user_id is not None and package["owner_id"] == user_id)
            or (user_id is not None and package_id in self.repository.shared_package_ids(user_id))
        ):
            raise HTTPException(404, "策略包不存在。")
        return package

    def _owned_package(self, package_id, owner_id):
        package = self.repository.get_package(package_id)
        if package is None or package["owner_id"] != owner_id or owner_id is None:
            raise HTTPException(404, "私有策略不存在或不属于当前用户。")
        return package

    def create_package(self, archive_b64, parameters, owner_id=None):
        metadata, prepared, archive = _open(self.engine, archive_b64, parameters)
        try:
            package = self.repository.create_package(
                metadata["name"], metadata["title"], metadata["description"],
                owner_id=owner_id, visibility="system" if owner_id is None else "private",
            )
        except StorageConflict as exc:
            raise HTTPException(409, "策略包名称或实现不能重复。") from exc
        version = self._store_version(package, metadata, prepared, archive)
        if owner_id is not None:
            self.repository.create_grant(version["id"], "development")
        return {**package, "version": _public_version(version)}

    def create_version(self, package_id, archive_b64, parameters, user_id=None, is_admin=False):
        package = self.repository.get_package(package_id)
        if package is None or not (
            (package["owner_id"] is None and is_admin)
            or (user_id is not None and package["owner_id"] == user_id)
        ):
            raise HTTPException(404, "没有权限修改该策略包。")
        metadata, prepared, archive = _open(self.engine, archive_b64, parameters)
        if metadata["name"] != package["code_key"]:
            raise HTTPException(422, "策略包名称与归档不一致。")
        version = self._store_version(package, metadata, prepared, archive)
        if package["owner_id"] is not None:
            self.repository.create_grant(version["id"], "development")
        return _public_version(version)

    def set_visibility(self, package_id, owner_id, visibility):
        self._owned_package(package_id, owner_id)
        self.repository.set_visibility(package_id, visibility)
        return self.repository.get_package(package_id)

    def list_shares(self, package_id, owner_id):
        self._owned_package(package_id, owner_id)
        auth = auth_repository(self.repository.database)
        return [
            {"id": user_id, "username": user["username"]}
            for user_id in self.repository.list_shares(package_id)
            if (user := auth.get_user_by_id(user_id)) is not None
        ]

    def share(self, package_id, owner_id, username):
        self._owned_package(package_id, owner_id)
        user = auth_repository(self.repository.database).get_user_by_username(username.strip())
        if user is None:
            raise HTTPException(404, "分享对象不存在。")
        if user["id"] == owner_id:
            raise HTTPException(409, "无需向自己分享策略。")
        try:
            self.repository.add_share(package_id, user["id"])
        except StorageConflict:
            pass
        return self.list_shares(package_id, owner_id)

    def unshare(self, package_id, owner_id, user_id):
        self._owned_package(package_id, owner_id)
        self.repository.remove_share(package_id, user_id)
        return self.list_shares(package_id, owner_id)

    def clone(self, package_id, owner_id, *, title=None):
        package = self._visible_package(package_id, owner_id)
        versions = self.repository.list_versions(package_id)
        if not versions:
            raise HTTPException(409, "该策略包尚无可复制版本。")
        source = versions[-1]
        if not source["archive"]:
            raise HTTPException(409, "该策略版本没有可复制的归档。")
        code_key = f"u{owner_id}_copy_{uuid4().hex[:16]}"
        name = title.strip() if title is not None else f"{package['name']}（副本）"
        try:
            archive = _rename_archive(source["archive"], code_key, name)
        except (ValueError, zipfile.BadZipFile, KeyError) as exc:
            raise HTTPException(409, "源策略包归档无法复制。") from exc
        try:
            copied = self.repository.create_package(
                code_key, name, package["description"],
                owner_id=owner_id, visibility="private",
            )
            new_version = self.repository.create_version(
                copied["id"], source["version"], source["parameters"],
                source["entry_point"], hashlib.sha256(archive).hexdigest(), archive,
            )
        except StorageConflict as exc:
            raise HTTPException(409, "策略复制发生名称冲突，请重试。") from exc
        if self.repository.find_grant(source["id"], "development"):
            self.repository.create_grant(new_version["id"], "development")
        return {**copied, "version": _public_version(new_version)}

    def archive(self, package_id, version_id, user_id):
        package = self._visible_package(package_id, user_id)
        if package["owner_id"] != user_id and package["visibility"] != "system":
            raise HTTPException(404, "仅策略所有者可下载私人归档。")
        version = self.repository.get_version(version_id)
        if version is None or version["package_id"] != package_id or not version["archive"]:
            raise HTTPException(404, "策略版本归档不存在。")
        return version["archive"], package["code_key"], version["version"]

    def grant(self, version_id, purpose):
        if purpose not in {"production", "development"}:
            raise HTTPException(422, "授权类型只能是正式或开发。")
        version = self.repository.get_version(version_id)
        if version is None:
            raise HTTPException(404, "策略版本不存在。")
        package = self.repository.get_package(version["package_id"])
        if package is None:
            raise HTTPException(404, "策略包不存在。")
        if (purpose == "production" and package["owner_id"] is None
                and package["code_key"] in _RESEARCH_ONLY_SYSTEM_BASKETS):
            raise HTTPException(409, "跨产品系统 Demo 仅支持开发研究，禁止正式授权。")
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

    def issue(self, tenant_id, account_id, version_id, overrides, user_id=None):
        tenant = self.tenants.require_active(tenant_id)
        version = self.repository.get_version(version_id)
        if version is None:
            raise HTTPException(404, "策略版本不存在。")
        package = self.repository.get_package(version["package_id"])
        if package is None:
            raise HTTPException(404, "策略包不存在。")
        if (tenant["purpose"] == "production" and package["owner_id"] is None
                and package["code_key"] in _RESEARCH_ONLY_SYSTEM_BASKETS):
            # Also block legacy grants that might have been created previously.
            raise HTTPException(409, "跨产品系统 Demo 无正式运行时，禁止加载生产账户。")
        if user_id is not None:
            self._visible_package(package["id"], user_id)
        if self.repository.find_grant(version["id"], tenant["purpose"]) is None:
            if tenant["purpose"] == "production":
                raise HTTPException(409, "正式租户加载需要单独的正式授权。")
            raise HTTPException(409, "该策略版本还不能加载到开发租户。")
        account = str(account_id).strip()
        self._require_account(tenant, account)
        if hashlib.sha256(version["archive"]).hexdigest() != version["package_hash"]:
            raise HTTPException(409, "策略包哈希不一致。")
        try:
            result = self.engine.inspect_strategy(
                base64.b64encode(version["archive"]).decode(),
                {**version["parameters"], **(overrides or {})}, tenant_id=tenant["id"],
            )
        except QueryError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, "策略校验 Engine 暂时不可用。") from exc
        metadata = result["metadata"]
        if (metadata["digest"] != version["package_hash"]
                or metadata["entry_point"] != version["entry_point"]
                or metadata["name"] != package["code_key"]):
            raise HTTPException(409, "策略归档的身份与已保存版本不一致。")
        parameters = result["parameters"]
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
                package["id"], metadata["version"], prepared, metadata["entry_point"],
                metadata["digest"], archive,
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

    def basket_backtest(
        self, tenant_id, account_id, mode, start, end, interval,
        starting_primary_balance, starting_perpetual_balance, max_gross_notional,
    ):
        """Only grant-scoped development research with a real virtual capital budget."""
        tenant = self.tenants.require_active(tenant_id)
        if tenant["purpose"] != "development":
            raise HTTPException(409, "跨产品组合回测仅支持开发租户。")
        account = self._virtual_account(tenant, str(account_id).strip())
        if account["quote_currency"] != "USDT":
            raise HTTPException(422, "跨产品组合仅接受 USDT 虚拟资金。")
        if mode not in {"funding_carry", "protected_options"}:
            raise HTTPException(422, "未知的研究组合模式。")
        try:
            amounts = (
                Decimal(str(starting_primary_balance)),
                Decimal(str(starting_perpetual_balance)),
                Decimal(str(max_gross_notional)),
            )
            initial = Decimal(str(account["initial_capital"]))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise HTTPException(422, "组合资金和风险预算格式无效。") from exc
        if (
            any(not value.is_finite() or value <= 0 for value in amounts)
            or not initial.is_finite()
            or amounts[0] + amounts[1] > initial
        ):
            raise HTTPException(422, "两独立账户初始资金必须为正，且总额不能超出虚拟账户资金。")
        try:
            self.engine.runnable(tenant["id"], "backtest")
            runner = (
                self.engine.run_funding_carry_backtest
                if mode == "funding_carry" else self.engine.run_protected_option_backtest
            )
            return runner(
                tenant["id"], account["id"], start, end, interval,
                format(amounts[0], "f"), format(amounts[1], "f"),
                format(amounts[2], "f"),
            )
        except EngineConflict as exc:
            raise HTTPException(409, exc.message) from exc

    def latest_basket_backtest(self, tenant_id, account_id, mode):
        tenant = self.tenants.require_tenant(tenant_id)
        self._virtual_account(tenant, str(account_id).strip())
        if mode not in {"funding_carry", "protected_options"}:
            raise HTTPException(422, "未知的研究组合模式。")
        try:
            getter = (
                self.engine.latest_funding_carry_backtest
                if mode == "funding_carry" else self.engine.latest_protected_option_backtest
            )
            found = getter(tenant["id"], str(account_id).strip())
        except EngineConflict as exc:
            raise HTTPException(409, exc.message) from exc
        if found is None:
            raise HTTPException(404, "还没有该组合的开发回测结果。")
        return found

    def development_worker_tenants(self):
        return [
            {"id": row["id"], "name": row["name"], "status": row["status"]}
            for row in self.tenants.repository.list_tenants()
            if row["purpose"] == "development" and row["status"] == "active"
        ]

    def _worker_tenant(self, tenant_id):
        tenant = self.tenants.require_active(tenant_id)
        if tenant["purpose"] != "development":
            raise HTTPException(409, "只有开发租户支持回测 Worker 配置。")
        return tenant["id"]

    def backtest_worker_settings(self, tenant_id):
        return self.engine.backtest_worker_settings(self._worker_tenant(tenant_id))

    def update_backtest_worker_settings(self, tenant_id, max_workers, timeout_seconds):
        return self.engine.update_backtest_worker_settings(
            self._worker_tenant(tenant_id), max_workers, timeout_seconds,
        )

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


def _open(engine, archive_b64, parameters):
    try:
        archive = base64.b64decode(archive_b64, validate=True)
        inspection = engine.inspect_strategy(archive_b64, parameters or {})
        metadata, prepared = inspection["metadata"], inspection["parameters"]
        if metadata["digest"] != hashlib.sha256(archive).hexdigest():
            raise HTTPException(409, "策略包哈希不一致。")
    except (ValueError, binascii.Error, QueryError) as exc:
        raise HTTPException(422, "策略包无效。" if isinstance(exc, binascii.Error) else str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "策略校验 Engine 暂时不可用。") from exc
    return metadata, prepared, archive


def _public_version(version):
    return {key: value for key, value in version.items() if key != "archive"}


def _rename_archive(raw, new_code_key, title):
    """Copy package source without loading it or importing trading dependencies."""
    with zipfile.ZipFile(io.BytesIO(raw)) as original:
        entries = original.infolist()
        if len(entries) > 40 or sum(item.file_size for item in entries) > 2_000_000:
            raise ValueError("策略归档内容超过复制上限。")
        if any(item.filename.startswith("/") or ".." in Path(item.filename).parts
               for item in entries):
            raise ValueError("策略归档包含无效路径。")
        with io.BytesIO() as output:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as copied:
                for item in entries:
                    data = original.read(item)
                    if item.filename == "seealpha-package.json":
                        manifest = json.loads(data.decode("utf-8"))
                        manifest["name"] = new_code_key
                        manifest["title"] = title
                        data = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
                    copied.writestr(item.filename, data)
            archive = output.getvalue()
    if len(archive) > 300_000:
        raise ValueError("复制的策略包超过上传限制。")
    return archive
