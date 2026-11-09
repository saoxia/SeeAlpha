# 接入独立 seealpha-engine

原 SeeAlpha 项目迁移期仍保留 `engine/` 源码与联合测试，但**新的开发/部署路径不再从旧仓库构建或启动 Engine**。Engine 源码来自私有 [saoxia/seealpha-engine](https://github.com/saoxia/seealpha-engine)，Platform 仅通过 HTTP v2 接口及明确的容器镜像版本连接它。

注意：新 Engine 仍处于 [PR #1](https://github.com/saoxia/seealpha-engine/pull/1) 的过渡阶段，下面的提交是**未发布的开发候选版本**，不是可用于实盘的正式版本。当前不切换生产环境，不删除旧代码或迁移交易状态。

## 从新仓库构建镜像

在 SeeAlpha Platform 仓库根目录执行：

```bash
./scripts/build_external_engine.sh
```

脚本通过 Git 克隆独立 Engine 仓库，核对完整 SHA `9e670a6d2d4c0cce0a23bad0f40a99593687ae0e`，然后只使用新仓库的 Dockerfile、锁文件和源码构建 `seealpha-engine:9e670a6d2d4c`，**不会读取本仓库的 `engine/`、`pyproject.toml` 或 Dockerfile**。若要构建另一已审查的提交：

```bash
./scripts/build_external_engine.sh <40位Engine提交SHA>
```

脚本默认使用 GitHub SSH，需要预先配置私有仓库权限；若 Git 已设置好 HTTPS 凭据，可通过 `SEEALPHA_ENGINE_GIT_REMOTE=https://github.com/saoxia/seealpha-engine.git` 覆盖。须已安装 Git 和 Docker；外部包源不可用时，镜像构建可能失败。构建为**手动按需**，不会自动上传镜像、运行容器、写入密钥或更改生产数据。

## 模式 A：独立进程（隔离开发）

在独立 Engine 仓库的 checkout 中，使用新项目的 Python 依赖和私有令牌：

```bash
uv sync --locked
ENGINE_TOKEN='<随机私有开发令牌>' uv run python -m engine
```

在另一个终端切回 SeeAlpha Platform 仓库：

```bash
uv sync --locked --group api
export ENGINE_DEPLOYMENT=single
export ENGINE_URL=http://127.0.0.1:8766
export ENGINE_TOKEN='<相同的随机私有开发令牌>'
# 另行配置隔离开发 DATABASE_URL，然后启动 API
uv run python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

**不要同时启动**本仓库遗留的 `python -m engine`。当前 Platform 根目录锁文件仍含 NautilusTrader，这是待后续清理的开发依赖历史，不代表业务 API 会 import 或启动它。

## 模式 B：独立镜像（全新隔离测试租户）

先执行上面的构建脚本，再在全新测试数据库/测试租户环境下设置：

```bash
export ENGINE_DEPLOYMENT=docker
export ENGINE_DOCKER_IMAGE=seealpha-engine:9e670a6d2d4c
export ENGINE_TENANT_ROOT=/absolute/path/to/disposable-test-tenants
```

当前 Platform Docker 管理器只在同一宿主机管理 `seealpha-engine-<tenant_id>`；即使测试数据目录不同，也**不能复用现有正式租户 ID**。将 `ENGINE_DOCKER_IMAGE` 改为新值不会迁移旧容器。现在代码会在新旧镜像不一致时拒绝复用/连接，而非偷偷继续运行旧 Engine；停止、备份、核实未决订单及重建仍须人工批准。

正式镜像建议引用不可变 digest；本地提交 SHA 标签只用于迁移开发与验收。生产部署不应使用 `:latest` 作为跨仓库版本保证。

## 不启动真实交易的验证

原项目 `tests/api/engine_isolation_test.py` 已经将 `api/` 与 `core/` 复制到不含 `engine/` 的临时目录，独立导入 API：

```bash
uv run pytest -q tests/api/engine_isolation_test.py tests/api/tenant_docker_test.py
```

当新仓库 Engine 使用**隔离本地开发令牌和状态卷**启动后，可运行一条只读 HTTP v2 验证：

```bash
SEEALPHA_TEST_EXTERNAL_ENGINE=1 \
ENGINE_URL=http://127.0.0.1:8766 \
ENGINE_TOKEN='<随机私有开发令牌>' \
uv run pytest -q tests/api/external_engine_contract_test.py
```

测试只访问本地 loopback Engine 的 `/health`，验证 401、成功状态和 426，不创建租户、不读交易所凭据、不发订单。未设置开关时跳过。

## 剩余切割与部署边界

- 历史组合 `pyproject.toml`/`uv.lock`、单元测试内导入的 `engine.*`、原 CI 对 Engine 的构建依赖仍留在当前仓库；不在本次切换中直接删除或改动 CI。
- `core/engine_contracts.py` / `core/trading_contracts.py` 仍由两个仓库分别持有。正式升级前需对照接口版本和订单参数语义，做完整跨仓库集成测试。
- 生产切换前必须独立完成状态卷备份/恢复、未知订单核查、回滚、租户隔离及版本兼容验收；不得让两个 Engine 同时管理同一个真实账户。
