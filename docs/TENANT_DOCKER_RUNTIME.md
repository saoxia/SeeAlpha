# 每租户独立 Docker Engine（第一阶段）

此阶段将正式/开发租户的 NautilusTrader Engine 放入**独立、常驻**的 Docker 容器。
Web/FastAPI、用户权限、业务数据库和策略商店不进入租户容器。
业务 API 继续通过原有 `EngineClient` 的 HTTP 契约访问 Engine，但 Docker 模式按
`tenant_id` 路由到不同的容器。容器本身不暴露给公网，也不持有 Docker socket。

本阶段**不实现**共享数据缓存、数据预加载、分布式 Worker 或 DeepSeek Harness。
每个租户有独立的 `data/`，需要自行准备所需历史数据；重复回测仍由现有
`run_backtest()` 逐次从本租户 Catalog 读取。

## 准备镜像

需要安装 Git、Docker Engine、Docker CLI，并确保业务 API 的宿主机用户具有 Docker
操作权限（此权限等同于高权限，**不要**授予普通用户、策略代码或 Harness）。

```bash
./scripts/build_external_engine.sh
```

镜像直接从 `saoxia/seealpha-engine` 独立仓库的固定提交构建；新仓库访问要求、已验收版本和升级安全约束见 [跨仓库接入](EXTERNAL_ENGINE.md)。镜像包含 NautilusTrader / FastAPI、所需 Python 依赖和 release 版 Rust
`nautilus-data-converter`。部署新版本时重新构建镜像、按停机流程重建对应租户
容器；删除容器不会自动删除绑定的数据卷，删除租户时旧目录会进入 `.retired` 隔离区。

## 开启 Docker 部署

本轮按**全新部署**约定启用，不提供旧 Engine 目录或订单状态的自动迁移。
先初始化新的业务数据库和租户状态目录，再增加：

```bash
export ENGINE_DEPLOYMENT=docker
export ENGINE_DOCKER_IMAGE=seealpha-engine:01ad427c5bc5
export ENGINE_TENANT_ROOT=/absolute/path/to/seealpha/.local/tenants
export ENGINE_PRODUCTION_CPUS=2
export ENGINE_PRODUCTION_MEMORY=4g
export ENGINE_DEVELOPMENT_CPUS=4
export ENGINE_DEVELOPMENT_MEMORY=8g
# 然后按 api/README.md 中的命令启动业务 API
```

以上只是默认配额示例，须结合机器容量和实际内存峰值调整。
`ENGINE_DEPLOYMENT=single` 仍保留现有单 Engine 模式供开发使用；
Docker 模式不在失败时偷偷回退到生产进程内执行。Engine 默认的 `none` 只允许独立本机运行；Platform 创建的 Docker 必须显式启用 `token` 并注入强随机令牌。升级前创建的 `engine.env` 如缺少 `ENGINE_AUTH_MODE=token` 会拒绝复用，必须先执行受控停止、备份和重建；不会悄悄替旧容器修改启动环境。

首次 API 启动时会确保系统默认行情租户有一个独立 Engine；用户创建开发租户时仍按原流程部署。
**新增普通用户只创建正式租户的业务记录，不自动创建正式 Docker。** 管理员需先在
管理后台「用户管理」中为该用户选择本机或已连接的远程 Node，再点「部署 Docker」。
没有显式放置记录的普通用户正式租户不会被后台自动部署。选定节点后，后台同步可以在
节点暂时不可用时继续重试，但不自动改换服务器。
开发租户归档会先导出 Engine 切片，然后停止并移除容器；恢复时从持久状态卷
重建容器并导入切片。删除租户会停止并移除容器，随后将其旧数据、日志、令牌和状态目录
**整体隔离保留**在 `.local/tenants/.retired/<tenant_id>-<随机标识>/`；
不会自动永久删除这些文件。这样即使业务数据库重新使用同一租户 ID，
新租户也不会继承旧用户的策略、行情或凭据。归档租户不移动目录，仍可恢复。

Docker 使用 `--cpus`、`--memory`、`--memory-swap`、`--pids-limit`，
并去掉 Linux capabilities、启用 `no-new-privileges`。
这是资源约束，并不是对任意不可信策略代码的完整安全沙箱；
目前仍应只加载受信任、经审核的策略包。
单机多个容器仍会共享宿主机 I/O；对低延迟生产有严格要求时需部署在独立主机。

## 存储和网络

所有租户（含初始正式租户）的宿主机持久目录：

```text
.local/tenants/<tenant_id>/
  engine.env       # 0600, 此租户专属 Engine 令牌及固定身份
  state/           # Engine SQLite WAL 订单/成交/持仓/幂等事实
  data/            # 独立 raw、Catalog 和行情数据，保留在宿主机
  local/           # 容器内部 .local/run
  logs/            # 子进程日志
```

**没有默认租户存储特例。** 每个 Engine 都只挂载 `.local/tenants/<tenant_id>/data/` 为
`/app/data`，不额外挂载旧项目 `data/` 或旧 API 数据任务目录。现有全局行情管理
API 仍选择业务库里 `system_key=default` 的租户作为请求目标，属于尚待按租户改造
的业务路由，而不是特殊的存储或执行方式。

开发租户的历史 Catalog 不会自动复制。准备策略回测前，应当在其独立数据目录
中生成或**复制属于该租户的数据**。如原始数据保存在项目 `data/catalog`，
在授权使用的前提下，可在关闭有关读写任务时手动复制到
`.local/tenants/<tenant_id>/data/catalog`。没有该数据时，现有回测会继续
明确报“指定区间没有可用的本地 K 线”，不会回退到另一个租户的 Catalog。

每个 Engine 内部仍监听 `8766`，Docker 仅把它映射到宿主机
`127.0.0.1` 随机端口，由 API 发现。各租户使用 Platform 生成的独立 Bearer 令牌（`ENGINE_AUTH_MODE=token`），
Engine 还校验 URL 里的租户 ID 和登记用途。不要在外部反向代理或防火墙上
公开这些随机端口，也不要将 Engine 令牌交给浏览器。
`ENGINE_DEPLOYMENT=docker` 管理**同一 Docker 宿主机**。`ENGINE_DEPLOYMENT=distributed`
可以通过出站 Node Agent 管理远端开发或正式租户（含默认行情租户），
具体节点绑定、远端不支持的流式能力与数据迁移约束见 [Node Agent](NODE_AGENT.md)。
不得暴露 Docker daemon TCP 端口。

## 全新部署与数据保护

不沿用旧数据库和租户状态卷；执行新版本 Alembic 迁移后再创建账号与 Engine。
本轮清理不自动复制旧 `data/` 或 `.local/state/engine/`。
如仍保留旧本地实验数据，请自行备份；不要在旧状态有未结订单或真实仓位时直接
复用交易账户。后续正式上线后，升级仍需要可靠的备份、恢复及停机流程。

普通用户由管理员通过 `POST /api/users` 创建并获得一个尚未部署的正式租户；
`GET /api/users/engine-nodes` 列出候选服务器，
`POST /api/users/{user_id}/engine` 携带 `{"node_id": "local"}` 或已注册的 Node ID 执行首次部署。
选择记录保存在 `ENGINE_TENANT_ROOT/.production-placements.json`（私有权限 0600）。
指定之后禁止在此 API 内直接改绑，应按受控迁移程序处理数据、未决订单和真实仓位。
普通用户可创建开发租户。
为了保持安全边界，目前正式租户不能直接删除或修改用途；资源限制统一由
部署管理器按用途配置。此处尚未实现 per-user 配额与多实例调度。

## 测试

```bash
# Platform-owned checks (no Engine implementation dependency)
platform/.venv/bin/python -m ruff check api/config.py api/main.py api/engine_client.py api/tenant_docker.py platform/tests/tenant_docker_test.py
platform/.venv/bin/python -m pytest -c platform/pyproject.toml -q platform/tests/tenant_docker_test.py
# Legacy combined regressions (until their integration fixtures move to Engine/HTTP)
uv run pytest -q tests/api/tenants_test.py \
  tests/api/user_tenants_test.py tests/engine/phase1_test.py tests/engine/backtest_test.py
# Original API backtest assertions are now a disposable external-Engine acceptance:
SEEALPHA_VERIFY_PLATFORM_BACKTEST=1 ./scripts/verify_external_engine.sh
# Engine-owned fixed-tenant HTTP guard: seealpha-engine/tests/engine/tenant_http_isolation_test.py
```

PR CI 还包含 Docker 镜像构建及租户身份绑定的 HTTP 冒烟检查。
