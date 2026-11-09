# Platform 独立 Python 运行环境

当前根目录的 `pyproject.toml` 与 `uv.lock` 暂时保留，供旧 Monorepo 联合测试使用；新的 Platform Python 运行环境**单独管理于 `platform/`**。本目录不复制业务源码：实际服务继续使用同一仓库根目录的 `api/`、`core/` 和 `migrations/`，浏览器前端仍是 `web/`。

独立环境的运行依赖只有业务 API 直接需要的 SQLAlchemy、Alembic、psycopg、FastAPI、Uvicorn、HTTPX、Argon2 与 cryptography；不安装 NautilusTrader、NumPy、Pandas、PyArrow、WebSockets、研究可视化库或交易引擎。开发环境额外安装 pytest、Ruff。完整版本通过本目录的 `uv.lock` 锁定。

## 安装及边界验证

在仓库根目录运行：

```bash
./scripts/check_platform_runtime.sh
```

脚本在 `platform/.venv` 安装锁定依赖并运行 `platform/tests/`。测试明确检查 NautilusTrader 分发包未安装，并在仅包含 `api/` 与 `core/` 的临时目录中真正加载 Platform ASGI app。整个检查不启动 Engine、不使用生产数据库、不向交易所发请求、不触碰运行状态。严格的 `uv sync --locked` 不会擅自重新解析依赖；若需要更改版本，必须单独批准并更新此处锁文件。

安装或仅启动业务 API：

```bash
uv sync --project platform --locked --no-default-groups
# 确保独立 Engine 在 127.0.0.1:8766 启动，且 Token 相符
export ENGINE_URL=http://127.0.0.1:8766
export ENGINE_TOKEN='<独立 Engine 的私有令牌>'
export DATABASE_URL='<另行配置且已迁移的业务数据库连接串>'
platform/.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

命令需从 **SeeAlpha 仓库根目录**运行，以便发现 `api/` 和 `core/`。必须使用真实但隔离的业务数据库配置，且不能与当前正在服务的业务实例重复运行定期任务或连接同一账户；这里仅提供部署说明，不要求执行。

## 旧 Monorepo 迁移债务

- 现有 `tests/api/conftest.py` 会导入 `engine.app` 和 `engine.config`，不能直接在瘦身环境运行整个 `tests/api`；新独立测试在 `platform/tests/`，旧联合回归暂留根环境，直到改写为跨进程假服务/真实测试容器。
- `tests/api/backtest_test.py` 还依赖 `tests/engine/` 的数据构建辅助。移除旧 Engine 源码前必须将该辅助变成独立测试夹具或跨仓库验收素材，而不是直接删除有效测试。
- 原仓库 `docker/engine.Dockerfile`、根 `pyproject.toml`/`uv.lock` 仍为历史构建入口，当前统一对外构建流程应使用 `scripts/build_external_engine.sh`，勿依赖这些旧构建产物。
- `engine/` 的 82 个源文件在这一过渡 PR 中不删除。行情、策略、回测、状态恢复的完整业务链路验收仍须单独完成。

## Platform 独立 HTTP / Docker 路由单元测试

原 `tests/api/tenant_docker_test.py` 的 HTTP 客户端、租户路由、旧镜像拒绝、Docker 本地路径与租户文件归档等纯 Platform 测试，已经迁入 `platform/tests/tenant_docker_test.py`。旧 `platform/tests/engine_http_routing_test.py` 中独有的断连失败关闭测试也已合并，重复断言不再分散在两个文件。原测试中直接创建 Engine ASGI app 的固定租户 HTTP 隔离用例已迁至独立 `saoxia/seealpha-engine` 的 `tests/engine/tenant_http_isolation_test.py`。这一批迁移不需要在 Platform 测试环境中安装 NautilusTrader 或运行 Docker；仅改变测试归属，旧业务代码未修改。

这不等于迁移了真实的行情、策略和回测集成测试；那些用例仍由单独的 Engine/跨仓库测试负责。

## 行情消费端测试归属

行情数据读取的 HTTP 委派测试已从 `tests/api/market_data_remote_test.py` 移至
`platform/tests/market_data_remote_test.py`。原
`tests/api/market_data_engine_ownership_test.py` 中不需要 Engine 实现的 11 项测试已迁至
`platform/tests/market_data_engine_ownership_test.py`；仅保留真正比较 Engine 与 Platform
锁生命周期的联合测试在旧目录。原 `tests/api/engine_isolation_test.py` 的源码导入静态
规则也复制到 `platform/tests/runtime_boundary_test.py`，以便在没有 NautilusTrader 的依赖环境中执行。

`./scripts/check_platform_runtime.sh` 运行这批 Platform 测试；根目录旧测试继续负责
需要 Engine/行情 Catalog/策略执行环境的测试。**单独运行 `uv run pytest` 不能代表
两套独立测试全部通过**，后续集中验收应同时运行两套。迁移只改变测试归属，未改生产代码。

## 跨进程独立 Engine 验证

原 `tests/api/external_engine_contract_test.py` 已迁移至
`platform/tests/external_engine_contract_test.py`：它仅通过独立 HTTP 服务访问 Engine，
不再触发旧 `tests/api/conftest.py` 的自动进程内 Engine fixture。
正常执行 Platform 测试时，该网络验证因开关关闭而跳过。

在**隔离 Docker 主机**上，`./scripts/verify_external_engine.sh` 优先使用
`platform/.venv`（若尚未安装则退回根目录旧环境完成历史只读握手）。
需要额外检查临时租户创建、重复幂等操作、开发租户禁止实盘及空账本隔离时，
可在集中验收阶段执行：

```bash
uv sync --project platform --locked
SEEALPHA_VERIFY_BUSINESS=1 ./scripts/verify_external_engine.sh
```

此增强模式**只在脚本临时创建的容器和独立临时状态卷内**写入一个开发租户。
它不读取或保存交易所凭据，不发单，不下载行情、不跑策略和回测。
不能把此基础业务边界验证当成完整业务链路验收。

旧 `tests/api/conftest.py` 仍服务于依赖 Engine 实现与真实回测数据的联合回归；
其 Engine 实现导入已下沉到夹具执行时，而不在测试收集阶段加载。
旧联合测试完全改为跨进程前不能删除旧 `engine/`。

## 策略与异步回测的业务端测试

`tests/api/async_tasks_test.py` 已迁入独立 `platform/tests/async_tasks_test.py`。
原测试需要的业务数据库和管理员会话在专用临时 SQLite 中初始化；只测试路由鉴权、
任务提交、结果获取、取消和重试，因此故意关闭后台启动与 Engine 生命周期，
并对任何意外的 Engine HTTP 调用直接报错。不依赖根目录的 `tests/api/backtest_test.py`
和旧 `tests/api/conftest.py`。

`platform/tests/strategy_policy_test.py` 包含迁移的策略模块源码边界检查，以及
开发租户回测权限、账户所有权、资金/参数转发、异步幂等键透传、正式租户未授权
策略不可下发等隔离的 Platform 业务单元测试。它们不代替真正的 Engine 策略包
加载、回测 Catalog、交易所执行测试；这些仍在旧联合测试与独立 Engine 仓库。

## 旧联合测试的策略包与 Catalog 夹具分离

历史联合回归现在从 `tests/fixtures/strategy_packages.py` 生成确定性的策略包 ZIP，
它只使用 Python 标准库，**不导入 Engine、NautilusTrader 或 Engine 测试文件**。
Engine 侧测试断言生成字节与实际 `archive_directory` 相同，避免替换素材时改变策略身份摘要。
原 `tests/api/strategy_store_test.py` 继续进行真实策略加载和协调预览断言，
但已不再从 `tests/engine/strategy_load_test.py` 导入生成素材。

包含真实 Nautilus K 线对象与 Parquet Catalog 的构建器现归
`tests/fixtures/engine_catalog.py`，仅供旧联合回归与 Engine 侧测试使用，
它不属于 Platform 独立运行环境。
原 `tests/api/backtest_test.py` 已不再导入任何 `tests.engine.*` 模块，
但仍会在测试期间通过 Engine 专用 Catalog 夹具加载实际行情，因此**不能迁入
`platform/tests/`**，直至该部分改为独立 Engine HTTP + 共享临时 Catalog 验证。

## 通过独立 Engine 实测历史回测（集中验收时运行）

`SEEALPHA_VERIFY_BACKTEST=1 ./scripts/verify_external_engine.sh` 会在临时 Docker
Engine 容器内生成合成 Parquet Catalog，隔离的 Platform `EngineClient` 仅通过 HTTP
下发实际示例策略并运行 NautilusTrader 回测。输出校验资金、订单、成交、净持仓、
策略包身份、订阅门禁、租户隔离与最新记录，不使用真实行情或交易所。

详情与安全限制见 [独立 Engine 跨仓库验收](../docs/EXTERNAL_ENGINE.md)。
该检查**不是**独立 `platform/tests/` 的普通单元测试，需要显式设置开关并在隔离 Docker
主机上执行；当前只提交代码，执行状态仍为“待集中验收”。
