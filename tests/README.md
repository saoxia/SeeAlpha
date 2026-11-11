# 项目测试（Phase 20）

业务端日常测试在 [`platform/tests/`](../platform/tests/) 内，不再依赖
NautilusTrader、旧 `engine/` 目录或根目录重型 Python 环境。
`tests/legacy_engine_integration/` 则为过渡期联合兼容测试；
GitHub Actions 从固定的独立 Engine 提交加载 Engine，实现等价兼容覆盖。
原生 Engine 自己的回归由 [seealpha-engine](https://github.com/saoxia/seealpha-engine) CI 负责。

```text
tests/
├── api/                         # 轻量隔离和显式 Docker 集成入口
├── core/                        # 共用 Engine/Platform 合约兼容验证（CI）
├── legacy_engine_integration/   # 通过独立 Engine 检出的过渡联合回归
├── fixtures/                    # 独立测试素材
└── web/                         # 项目检查、日志、文件和锁
```

```bash
# 普通 Platform 回归（无需本机 Engine）
uv sync --project platform --locked
platform/.venv/bin/python -m pytest -c platform/pyproject.toml -q platform/tests
platform/.venv/bin/ruff check .

# 真实跨进程验收：仅隔离本机 Docker，默认不交易
./scripts/verify_external_engine.sh
```

全量兼容回归另由 GitHub Actions 检出固定 Engine 并安装其独立锁文件。
根目录 `pytest.ini` 和 `ruff.toml` 仅保存测试、代码风格设置。
不要在 Platform 业务运行环境安装研究依赖，也不要将本仓库的测试
当作独立 Engine 原生测试的替代。


`tests/baselines/lint_baseline.json` 当前没有逐文件样式例外。仍在仓库里的已授权源码变更登记在 `tests/baselines/source_updates.json`。
`tests/__init__.py` 固定本项目测试包的来源，测试间引用辅助函数时使用 `tests.` 前缀。
策略包约定见 [双均线示例](../packages/ema_crossover/README.md)。

租户 Docker/HTTP 客户端的独立测试已迁至 [`platform/tests/tenant_docker_test.py`](../platform/tests/tenant_docker_test.py)，固定租户 Engine ASGI HTTP 安全测试已迁至独立 `seealpha-engine/tests/engine/tenant_http_isolation_test.py`。旧联合目录不再包含 `tenant_docker_test.py`。

异步回测与任务状态的 Platform 路由测试已迁至
[`platform/tests/async_tasks_test.py`](../platform/tests/async_tasks_test.py)，
策略模块无 Engine 源码导入检查已迁至
[`platform/tests/strategy_policy_test.py`](../platform/tests/strategy_policy_test.py)。
旧联合测试只保留确实依赖 Engine 实现、策略包示例或回测 Catalog 的情形。

策略 ZIP 测试素材生成已独立到 `tests/fixtures/strategy_packages.py`（仅标准库）；
NautilusTrader Catalog 写入专属于 `tests/fixtures/engine_catalog.py`。
旧 API 策略和回测测试不再导入 `tests.engine.*` 测试用例模块。
真实策略目录/下发及历史回测已迁为显式跨进程验收脚本；其余旧联合测试继续保留。

原 `tests/api/backtest_test.py` 的真实业务 API 回测断言已迁至
`scripts/verify_external_platform_backtest_api.py`。集中验收须显式运行
`SEEALPHA_VERIFY_PLATFORM_BACKTEST=1 ./scripts/verify_external_engine.sh`，
原联合 `pytest` 不再包含这一用例；它仍与
`platform/tests/` 普通业务单元测试和独立 Engine 测试互补。

原 `tests/api/strategy_store_test.py` 的两个场景现在位于
`scripts/verify_external_platform_strategy_api.py`，保留原有真实策略包、
授权、跨租户和协调预览断言。测试在隔离 Platform API 与独立 Engine
HTTP 上运行，使用 `SEEALPHA_VERIFY_PLATFORM_STRATEGY=1` 显式开启。
特殊变更：原来读取 Platform 本地 `book.sqlite` 的凭据断言改为检查
独立 Engine 实际可导出的租户状态，不依赖 Engine 文件路径。

旧 `tests/api/conftest.py` 中的进程内 Engine HTTP 夹具现仅由明确标记的
联合模块按需启用，不再对整个 `tests/api/` 自动生效。
研究 Gateway/Grant 和历史 cashflow Alembic 升级测试已迁入
`platform/tests/` 并随 `./scripts/check_platform_runtime.sh` 集中运行；
根目录 `uv run pytest` 不会再运行这些迁移后的用例。

原 `tests/api/auth_test.py` 与 `tests/api/olap_test.py` 已迁入
`platform/tests/`，通过仅实现租户注册的严格 HTTP MockTransport
覆盖真实 Platform 生命周期。其他联合测试原先引用鉴权测试文件中的
`login` 函数，现在统一使用纯 HTTP 辅助函数
`tests/fixtures/api_auth.py`，避免再次导入 Platform 测试文件。

`tests/api/equity_test.py` 已整体迁入
`platform/tests/equity_test.py`，保留全部估值、重放、锁与历史曲线
断言；本模块原先借用的账户登录/创建测试辅助函数已本地化。
老联合套件不再承担这些 Platform 自有资产净值测试；
运行完整覆盖时必须包含独立 `platform/tests`。

旧管理任务动作 `backtest` 已从可提交任务列表移除；只读历史不受影响。
`tests/web/jobs_test.py` 额外覆盖对旧历史记录的可读取性，而正式回测
仍须使用独立 Engine 的显式跨进程验收脚本。


旧联合的期权业务 API 测试现在自行构造确定性合约元数据和 K 线，不再导入
已移入独立 Engine 仓库的 `tests.connectors` / `tests.pipeline` 测试模块；
这仍是过渡期的进程内测试，后续应迁为独立 HTTP 验收。
开发环境激活测试只验证 Platform 自有工具及缓存目录，不再要求旧 Rust
converter 的 `CARGO_TARGET_DIR`。

## Phase 19B：账户与交易 API 的测试归属

账户凭据校验、权益恒等式、无证据净值 K 线，以及交易参数、
账户环境、鉴权、市场选择器的纯业务回归，现由
`platform/tests/exchange_trading_boundaries_test.py` 验证。
测试只使用业务 API 与严格隔离的 Engine 启动桩，异常订单提交会立即失败。
旧联合目录继续测试原生下单、请求幂等、未知状态核对、账本恢复与锁；
未建立外部 Engine 等价验收前不删除这些测试。

## Phase 19 最终测试目录归属

- `platform/tests/`：不加载 Engine 源码、NautilusTrader 或生产账户，
  运行完整的 Platform 单元/HTTP 测试。
- `tests/legacy_engine_integration/`：仍必须使用旧 Engine ASGI、
  真实 Catalog、原生订单/资产桥接及文件锁的过渡兼容回归；
  8 个测试模块与专属 `conftest.py` 一同迁移，断言未删除。
- `tests/api/`：只保留旧架构隔离检查和显式 Docker 集成入口。
- `saoxia/seealpha-engine`：独立 Engine 自有的 Connector、Feed、
  Pipeline、Nautilus 及内核运行测试。

完整 Phase 19 测试归属已切开，但 `legacy_engine_integration`
的跨进程替代验收是 Phase 20 删除旧 Engine 之前的硬性前提。
旧 `engine/`、其本地 Dockerfile 和根目录重型依赖现已移除。

## Phase 19C：业务与 Engine 原生测试分离

已迁入 `platform/tests/` 的包括：17 个账本/资金费与操作同步用例、
8 个 ORM 持久化用例、5 个用户租户用例、1 个空模板用例、
10 个期权账本用例和 11 个管理 API 用例。
`tests/api/orm_storage_test.py`、`tests/api/user_tenants_test.py`
和 `tests/api/management_test.py` 已由独立业务回归替代，不再保留重复测试。

仍保留的联合测试必须具备真实 Engine 的功能依赖：原生订单事件转换、
期权标准化、行情文件与 Catalog 操作、撮合委托持久化/恢复、
交易所资产原生读写、归档时 Engine 状态转移。
这些需要跨进程 HTTP 验收或 Engine 仓库等价覆盖后才允许清除旧 Engine 源码。
不要将其误认为独立 Platform 运行时的依赖。

## 独立 Engine 专属测试的归属

原 `tests/connectors/`（13 文件）、`tests/feed/`（4 文件）和
`tests/pipeline/`（13 文件）已从本仓库删除。删除前以 Git blob SHA
核对，这 **30 个测试文件与独立 `saoxia/seealpha-engine` 的同路径文件内容完全一致**。
独立 Engine 仓库的 `.github/workflows/engine-ci.yml` 已在 PR、main push 上
执行 `uv run pytest tests/engine tests/connectors tests/feed tests/pipeline tests/core`
及 Rust converter 构建和单元测试。原仓库不再重复运行这三组纯 Engine 测试。

独立 Engine 原生测试现在完全由独立仓库维护；本仓库的联合兼容回归通过 CI
的固定 Engine 检出运行，不能仅凭 HTTP 单元 Mock 宣称交易行为已验证。**只执行 SeeAlpha 根目录 pytest 不等于
完整跨仓库验收**；必须区分 Platform、Engine 及显式 HTTP 集成验收。
