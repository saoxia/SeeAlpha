# 接入独立 seealpha-engine

原 SeeAlpha 项目迁移期仍保留 `engine/` 源码与联合测试，但**新的开发/部署路径不再从旧仓库构建或启动 Engine**。Engine 源码来自私有 [saoxia/seealpha-engine](https://github.com/saoxia/seealpha-engine)，Platform 仅通过 HTTP v2 接口及明确的容器镜像版本连接它。

注意：新 Engine 仍处于 [PR #1](https://github.com/saoxia/seealpha-engine/pull/1) 的过渡阶段，下面的提交是**未发布的开发候选版本**，不是可用于实盘的正式版本。当前不切换生产环境，不删除旧代码或迁移交易状态。

## 从新仓库构建镜像

在 SeeAlpha Platform 仓库根目录执行：

```bash
./scripts/build_external_engine.sh
```

脚本通过 Git 克隆独立 Engine 仓库，核对完整 SHA `b33769d432c17fff2b743c31c7efdd6bf3cb450d`，然后只使用新仓库的 Dockerfile、锁文件和源码构建 `seealpha-engine:b33769d432c1`，**不会读取本仓库的 `engine/`、`pyproject.toml` 或 Dockerfile**。若要构建另一已审查的提交：

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

**不要同时启动**本仓库遗留的 `python -m engine`。Platform 已增加 [`platform/README.md`](../platform/README.md) 中的独立运行环境与锁文件；原仓库根目录锁文件仍用于旧联合测试，不代表业务 API 会 import 或启动 NautilusTrader。

## 模式 B：独立镜像（全新隔离测试租户）

先执行上面的构建脚本，再在全新测试数据库/测试租户环境下设置：

```bash
export ENGINE_DEPLOYMENT=docker
export ENGINE_DOCKER_IMAGE=seealpha-engine:b33769d432c1
export ENGINE_TENANT_ROOT=/absolute/path/to/disposable-test-tenants
```

当前 Platform Docker 管理器只在同一宿主机管理 `seealpha-engine-<tenant_id>`；即使测试数据目录不同，也**不能复用现有正式租户 ID**。将 `ENGINE_DOCKER_IMAGE` 改为新值不会迁移旧容器。现在代码会在新旧镜像不一致时拒绝复用/连接，而非偷偷继续运行旧 Engine；停止、备份、核实未决订单及重建仍须人工批准。

正式镜像建议引用不可变 digest；本地提交 SHA 标签只用于迁移开发与验收。生产部署不应使用 `:latest` 作为跨仓库版本保证。


## 一键跨仓库容器验收（仅本机 Docker / 隔离环境）

准备好私有仓库 Git 权限、Docker Desktop/本机 Unix Socket 与 Platform 虚拟环境（`uv sync --locked --group api`）后，运行：

```bash
./scripts/verify_external_engine.sh
```

该脚本按锁定 SHA **重新**从独立 Engine 仓库构建镜像；先比较两仓库的 `core/engine_contracts.py` 与 `core/trading_contracts.py`，若发生漂移直接拒绝构建。然后在临时目录中只复制 `api/` 和 `core/`，启动单独命名的测试 Engine 容器，挂载全新临时 `state/data/local/logs` 卷、生成临时私有 Token，随机绑定 **127.0.0.1** 本地端口。

隔离进程将实际导入 Platform ASGI app 和 `EngineClient`，确保没有载入 `engine` 或 `nautilus_trader`，并向新 Engine 测试 HTTP 200、401、403、426 及 v2 契约响应。**只发送 GET 请求**；不会访问交易所、使用生产账户、创建租户、运行策略或写入生产数据。完成后自动删除这次生成的容器、临时令牌和临时数据卷；已有 `seealpha-engine-<tenant_id>` 不受影响。

此脚本拒绝远程 TCP/SSH Docker context。默认来源是迁移候选提交，切换到其他候选时可传完整 40 位 SHA：

```bash
./scripts/verify_external_engine.sh <完整40位Engine提交SHA>
```

**验收边界**：通过它只说明新 Engine 能独立构建、启动，且去掉旧源码的 Platform 能通过 HTTP v2 握手。不能替代真实数据任务、历史回测、生产交易状态恢复、升级/回滚和真实账户验证。

## 独立容器中的开发租户边界检查（可选）

保留默认只读 HTTP 握手；需要进行一次非实盘、非回测的业务边界验收时：

```bash
uv sync --project platform --locked
SEEALPHA_VERIFY_BUSINESS=1 ./scripts/verify_external_engine.sh
```

此模式强制使用独立 `platform/.venv`；脚本先按原流程构建独立 Engine、创建一次性容器、独立挂载状态卷和随机私有令牌，再通过去掉 `engine/` 源码的 Platform `EngineClient` 完成开发租户创建/幂等、回测权限读取、实盘交易门禁、空订单/成交/持仓记录及跨租户访问拒绝。执行结束自动清理。**仅临时测试容器发生状态写入，不会执行任何交易、下载行情或调用交易所。** 此增强测试没有在本轮开发环境运行，须在集中验收时执行。


## 真实 Nautilus 历史回测的跨进程验收（可选）

此模式不是简单的 HTTP Mock。它在全新临时 Docker Engine 的 `/app/data/catalog`
生成一段 **19 根合成 ETHUSDT 1m K 线**（2024-01-01），只通过 Platform 的
`EngineClient` HTTP v2 下发 EMA 示例策略，并执行真正的 NautilusTrader
历史回测：

```bash
uv sync --project platform --locked
SEEALPHA_VERIFY_BACKTEST=1 ./scripts/verify_external_engine.sh
```

基础只读握手仍然先执行；只有设置 `SEEALPHA_VERIFY_BACKTEST=1` 才会进入这一阶段。
脚本要求独立 `platform/.venv`，容器与所有卷由父脚本创建并在退出时销毁。
Catalog 生成脚本通过 `docker exec -i` 在独立 Engine 容器内部运行；
Platform 的 Python 环境**不安装、不导入 NautilusTrader 或旧 `engine/`**。

该验收检查：策略包哈希与单租户下发、开发租户不得进行实盘、无策略及无订阅
不能回测、初始资金和结束资金、目标持仓轧差、订单/成交数量与标记、
最新回测记录、跨账户/跨租户隔离，以及模拟回测不写入真实交易订单账本。
合成数据无需交易所账号或联网行情；不会提交真实订单。

此项**尚未执行实机**，只能在下一次集中验收成功后记为通过；也不能替代
实际市场数据采集、分布式策略部署、状态恢复、异常恢复和生产发布前的完整
业务链路验收。

## 不启动真实交易的验证

原项目 `tests/api/engine_isolation_test.py` 已经将 `api/` 与 `core/` 复制到不含 `engine/` 的临时目录，独立导入 API：

```bash
uv run pytest -q tests/api/engine_isolation_test.py
platform/.venv/bin/python -m pytest -c platform/pyproject.toml -q platform/tests/tenant_docker_test.py
```

当新仓库 Engine 使用**隔离本地开发令牌和状态卷**启动后，可运行一条只读 HTTP v2 验证：

```bash
SEEALPHA_TEST_EXTERNAL_ENGINE=1 \
ENGINE_URL=http://127.0.0.1:8766 \
ENGINE_TOKEN='<随机私有开发令牌>' \
platform/.venv/bin/python -m pytest -c platform/pyproject.toml -q platform/tests/external_engine_contract_test.py
```

测试只访问本地 loopback Engine 的 `/health`，验证 401、成功状态和 426，不创建租户、不读交易所凭据、不发订单。未设置开关时跳过。

## 剩余切割与部署边界

- 历史组合 `pyproject.toml`/`uv.lock`、单元测试内导入的 `engine.*`、原 CI 对 Engine 的构建依赖仍留在当前仓库；不在本次切换中直接删除或改动 CI。
- `core/engine_contracts.py` / `core/trading_contracts.py` 仍由两个仓库分别持有。正式升级前需对照接口版本和订单参数语义，做完整跨仓库集成测试。
- 生产切换前必须独立完成状态卷备份/恢复、未知订单核查、回滚、租户隔离及版本兼容验收；不得让两个 Engine 同时管理同一个真实账户。

## 长期复用 Engine：同步与连续异步回测

`SEEALPHA_VERIFY_BACKTEST=1` 除了执行单次真实历史回测，还会在**同一个**
测试 Engine 容器内提交两个带不同初始资金、不同虚拟账户的异步任务。
通过既有 `/tenants/{id}/jobs` 队列提交，并检查：

- 同一幂等键重试返回原任务 ID，不重复计算；不同任务拥有独立 ID
- 两个任务分别完成，且结果文件、初始资金、订单与成交不会互相覆盖
- 查询各自账户的 latest backtest，能得到各自结果；模拟结果不会写入实盘账本
- 前后 Docker 容器内的 Engine 主进程 PID 相同，无需启动第二个容器

本次只强化**验收脚本**，不引入新的 Docker 调度器。生产回测的 Engine
已持有持久队列（默认容量 16、一个执行线程），每次 Nautilus 回测仍创建独立的
`BacktestEngine` 上下文。**批量数百/数千个任务的生产调度、并行计算、强制超时与
资源治理仍需后续独立设计**，不能将现有单线程队列解读为已支持大规模并发。

## 真实业务 API 回测联合测试的跨仓库迁移

原 `tests/api/backtest_test.py` 的 27 条业务断言已移至
`scripts/verify_external_platform_backtest_api.py`，执行真实登录、正式/开发
租户限制、注册与授权策略包、虚拟账户下发、历史行情订阅、回测、资金和
订单/成交结果、跨账户隔离、最新回测以及归档后拒绝回测。

集中验收使用：

```bash
uv sync --project platform --locked
SEEALPHA_VERIFY_PLATFORM_BACKTEST=1 ./scripts/verify_external_engine.sh
```

保留原有固定租户的只读 HTTP 200/401/403/426 握手容器，并另外启动
**一次性、允许多个测试租户**的 Engine 容器，用于原测试的租户对照场景。
两者都使用各自随机 Token、独立数据和状态卷、127.0.0.1 绑定；
脚本退出自动清理。业务 API 只加载复制后的 `api/`、`core/`、
`migrations/`、Alembic 配置和 EMA 示例文件，数据库为全新临时 SQLite。
测试进程不继承宿主机数据库 URL、交易所密钥或其他生产配置；
仅禁用 API 后台定时任务，**不 Mock API 路由、ORM、Engine HTTP
或 Nautilus 回测**。

这两个容器只用于一次集成验收，不代表正式回测每次新建 Docker。
普通回测继续复用常驻 Engine。旧联合 pytest 不再包含原回测用例，
因此在集中验收前，不得宣称原 API 回测链路迁移已通过。
