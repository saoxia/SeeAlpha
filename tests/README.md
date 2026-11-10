# 项目测试

独立 Engine 的 HTTP v2 真实联调已移至 [`platform/tests/external_engine_contract_test.py`](../platform/tests/external_engine_contract_test.py)，不再被本目录旧进程内 Engine fixture 自动包装。现有联合回归仍用旧实现直到涉及行情、策略和回测的用例完成跨进程迁移。

Platform 纯业务端测试已经逐步迁至 [`platform/tests/`](../platform/tests/)；
旧 `tests/` 暂保留需要 Engine 实现的联合用例。根目录 `uv run pytest`
**不会**自动覆盖 Platform 专用环境下的测试，见 [`platform/README.md`](../platform/README.md)。

测试集中存放，按业务模块分组，不要求与每个实现文件一一对应。

```text
tests/
├── api/                               # 迁移期仍需要旧 Engine 的联合 API 回归
├── engine/                            # 尚待迁移的原有 Engine 联合测试
├── fixtures/                          # 迁移期的通用测试素材
├── web/                               # 管理任务执行与 HTTP 访问边界
├── core/                              # 公共基础能力与开发环境
└── baselines/                         # 当前源码基线和授权更新清单
```

在项目根目录运行：

```bash
uv run pytest
uv run pytest tests/engine/strategy_load_test.py
# 独立 Engine 的真实策略 API 回归需要隔离 Docker 主机：
SEEALPHA_VERIFY_PLATFORM_STRATEGY=1 ./scripts/verify_external_engine.sh
```

测试文件保持 `*_test.py` 命名；pytest 的 `importlib` 模式避免不同目录的同名文件冲突。

`python -m pytest -q` 运行全量测试，包含 `source_baselines_test.py` 的历史来源核验；
`python -m ruff check .` 独立检查代码规范。Web 管理端可顺序运行两者并保存报告。


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


## 独立 Engine 专属测试的归属

原 `tests/connectors/`（13 文件）、`tests/feed/`（4 文件）和
`tests/pipeline/`（13 文件）已从本仓库删除。删除前以 Git blob SHA
核对，这 **30 个测试文件与独立 `saoxia/seealpha-engine` 的同路径文件内容完全一致**。
独立 Engine 仓库的 `.github/workflows/engine-ci.yml` 已在 PR、main push 上
执行 `uv run pytest tests/engine tests/connectors tests/feed tests/pipeline tests/core`
及 Rust converter 构建和单元测试。原仓库不再重复运行这三组纯 Engine 测试。

仍在 `tests/engine/` 中、尚供旧 API 联合测试使用的用例和工具不会因为
这次清理被删除；旧 Engine 源码、Docker 构建、根目录组合依赖也暂时保留，
直到对应调用链解除并统一验收。**只执行 SeeAlpha 根目录 pytest 不等于
完整跨仓库验收**；必须区分 Platform、Engine 及显式 HTTP 集成验收。
