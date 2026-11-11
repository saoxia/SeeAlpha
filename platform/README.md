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

## 数据库与部署边界回归

`platform/tests/database_boundaries_test.py` 持有从旧 `tests/api/orm_storage_test.py`
迁出的 7 项无需 Engine 的数据库与部署边界测试：拒绝未迁移数据库、
禁止进程内 Engine 回退、缺少显式数据库配置、拒绝混入旧业务表、
数据库连接文件权限和 PostgreSQL 握手超时的凭据脱敏。
需要 PostgreSQL 的用例仍依赖隔离的 `TEST_POSTGRES_URL`，未配置时明确跳过。
这些用例只使用 Platform 依赖，不启动 Docker 或独立 Engine。

`.github/workflows/tenant-engine.yml` 对 Python/Platform 变更运行单独
`platform_regression`，使用 `platform/uv.lock` 和 `platform/tests/`。
旧根目录 pytest 只覆盖尚未迁移的联合测试，不能代替此独立套件。

## 账户与手动交易的 Platform 边界回归

`platform/tests/exchange_trading_boundaries_test.py` 从旧 `tests/api/exchanges_test.py`
和 `tests/api/trading_test.py` 迁入 6 组无需 Engine 实现的原有测试：
API Key/Secret 相同的拒绝与脱敏、无原始证据时净值不造假、
权益 K 线不伪造样本、委托参数与账户环境前置校验、交易市场选择器鉴权和
完整市场列表。另补充了交易路由对未登录、只读用户、非法 Origin 以及
账户版本过期的拒绝断言。

测试运行真实 Platform API、临时业务数据库及会话鉴权，通过严格
`platform_engine_bootstrap` 隔离启动期 Engine HTTP 请求，并将任何
意外的订单提交视为测试失败；没有本地 Engine ASGI、旧 Binance Connector、
NautilusTrader、真实订单或 Docker。
原 Engine Native 执行、订单幂等保留、跨进程锁与未知订单恢复测试
仍在联合回归中，后续应由独立 Engine 或跨进程 HTTP 验收接替。

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
旧策略加载与协调预览断言现已迁至独立 HTTP 联调脚本
`scripts/verify_external_platform_strategy_api.py`；只使用标准库策略素材，
不再依赖 `tests/engine/strategy_load_test.py`。

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

## 长期容器复用与任务隔离

跨仓库回测脚本现在额外验证两个异步任务在**同一个**独立 Engine Docker 中
顺序完成，分别保留虚拟账户、初始资金与回测结果；任务重复提交遵守幂等性。
脚本比较回测前后的容器 Engine PID，不允许通过反复重启容器使验收通过。

普通回测复用已部署的 Engine；只有一次性集成验收才创建并清理 Docker。
现有任务队列每 Engine 默认容量 16、一个 worker，不代表已具备并行分布式
回测调度能力。见 [跨仓库验收说明](../docs/EXTERNAL_ENGINE.md)。

## Platform 业务 API 回测联合测试的迁移

原 `tests/api/backtest_test.py` 已由
`scripts/verify_external_platform_backtest_api.py` 接替；原 27 条业务断言
仍通过真实 API 路由执行。新脚本使用独立 `platform/.venv`、
隔离的 `api/core/migrations` 源码和一次性多租户独立 Engine Docker，
不会导入旧 `engine/` 或 `tests/fixtures/engine_catalog.py`。
`SEEALPHA_VERIFY_PLATFORM_BACKTEST=1 ./scripts/verify_external_engine.sh`
只在集中验收时显式运行，默认 Platform 单元测试不会启动 Docker。

## 真实 Platform 策略目录、授权及预览回归

原 `tests/api/strategy_store_test.py` 的两个端到端场景已迁至
`scripts/verify_external_platform_strategy_api.py`。它们保留策略 ZIP
安全检查、重复包和参数验证、授权与下发、跨租户隔离、真实模拟策略预览、
正式/开发租户的独立授权及账号归属等业务断言。

集中验收时：

```bash
uv sync --project platform --locked
SEEALPHA_VERIFY_PLATFORM_STRATEGY=1 ./scripts/verify_external_engine.sh
```

同时覆盖 Platform 策略与回测 API 可设置两个开关，复用**同一个**
一次性多租户测试 Engine 容器：

```bash
SEEALPHA_VERIFY_PLATFORM_STRATEGY=1 SEEALPHA_VERIFY_PLATFORM_BACKTEST=1 \
  ./scripts/verify_external_engine.sh
```

两个策略用例使用不同临时业务数据库，复用常驻 Engine，执行完一例后
删除仅属于一次性测试的 Engine 租户状态。原先无意义的本地 `book.sqlite`
扫描改成实际检查 Engine 导出的租户状态中不包含模拟加密凭据。
这些回归需要显式运行，不能算作普通 `pytest` 已覆盖；实机结果待集中验收。

## 纯 Platform 研究权限与历史业务库迁移测试

`research_gateway_test.py` 和 `research_grant_test.py` 现在直接属于
`platform/tests/`：它们通过 HTTPX MockTransport、临时 SQLite
和本地签名计算核对研究 Gateway 租约、令牌泄露防护、租户授权与拒绝门禁。
`cashflow_migration_test.py` 也已迁入独立 Platform 环境，使用真正
Alembic 历史版本逐级升级并检查旧账本证据和 CHECK 约束。
这些用例全部不导入 Engine/NautilusTrader，也不启动 Docker。

旧 `tests/api/conftest.py` 不再全局自动创建 Engine；需要它的传统
API 联合测试模块通过 `pytestmark = pytest.mark.usefixtures("engine_http")`
显式声明，避免纯数据库或静态隔离测试悄悄依赖 Engine。
集中验收需分别执行根目录剩余旧联合测试和 `./scripts/check_platform_runtime.sh`，
否则无法统计完整覆盖范围。

## 鉴权与 OLAP API 测试归属

`platform/tests/auth_test.py`、`platform/tests/olap_test.py` 已从旧 `tests/api`
迁出，保留真实 FastAPI / TestClient 生命周期、登录会话、Cookie 安全、
速率限制、密码重置、ClickHouse 设置加密与监控管理断言。

仅测试环境下通过 `platform/tests/conftest.py` 的
`platform_engine_bootstrap` 接管 EngineClient 的 HTTP 传输。
它**只允许**启动阶段的租户注册、空行情任务列表读取和空订阅策略
同步，且验证 HTTP 契约版本与本地测试 Token。任意策略下发、交易、
非空行情操作或未预期 Engine 请求均会报错。这样不需要在 Platform 测试中安装 NautilusTrader、
启动 Docker 或依赖旧 Engine ASGI fixture，也无需改动独立 Engine 仓库。
真实跨进程业务行为仍由显式集成验收脚本负责。

## Platform 资产净值（Equity）测试

原 `tests/api/equity_test.py` 的 **11 项测试函数** 已完整迁至
`platform/tests/equity_test.py`。估值、缺失行情证据、账户对账、
幂等事件、重放失败回滚、K 线生成、所有权锁及本地资产采集状态校准
均属于 SeeAlpha Platform 自有业务逻辑，不需要连接或修改 Engine。
原用例中只有涉及实际 FastAPI 登录与账户创建的 HTTP 场景显式使用
`platform_engine_bootstrap`；其余测试不实例化 Engine 客户端。

迁移后的测试独立生成临时 SQLite 数据库并通过原有 Alembic 版本升级。
原来借用 `tests.api.exchanges_test` 的 `signin/create` 小工具已替换
为等价的本模块纯 HTTP 辅助函数，防止 Platform 测试重新导入含有
Engine 本地实现的测试模块。真正交易所资产查询与委托执行仍必须通过
现有 Engine HTTP 契约单独验证。

## 已停用的本地研究回测管理动作

`/api/management/state.actions` 现在只列出仍实际可执行的项目检查动作；
旧 `backtest` 管理任务不再出现在动作目录，也不能启动。历史管理任务中
`backtest` 记录和旧报告仍可读取，不会删除业务状态、持久化文件或前端展示。
真正的回测继续通过开发租户的 `/api/tenants/{id}/accounts/{id}/backtests`
经独立 Engine HTTP 执行。这轮保留仍被 RESEARCH 页面使用的本地项目检查功能，
后续有替代 CI 状态接口后再移除执行器。
