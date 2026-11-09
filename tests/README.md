# 项目测试

独立 Engine 的 HTTP v2 真实联调已移至 [`platform/tests/external_engine_contract_test.py`](../platform/tests/external_engine_contract_test.py)，不再被本目录旧进程内 Engine fixture 自动包装。现有联合回归仍用旧实现直到涉及行情、策略和回测的用例完成跨进程迁移。

Platform 纯业务端测试已经逐步迁至 [`platform/tests/`](../platform/tests/)；
旧 `tests/` 暂保留需要 Engine 实现的联合用例。根目录 `uv run pytest`
**不会**自动覆盖 Platform 专用环境下的测试，见 [`platform/README.md`](../platform/README.md)。

测试集中存放，按业务模块分组，不要求与每个实现文件一一对应。

```text
tests/
├── pipeline/                          # 下载、校验、转换和数据准备
├── connectors/                        # 原生交易所适配器接入（实现位于 engine/connectors）
├── feed/                              # 连续行情、补洞、回放与就绪（实现位于 engine/feed）
├── engine/                            # 常驻引擎的事实、账户范围和 API 边界
├── web/                               # 任务执行与 HTTP 访问边界
├── core/                              # 公共基础能力与开发环境
└── baselines/                         # 当前源码基线和授权更新清单
```

在项目根目录运行：

```bash
uv run pytest
uv run pytest tests/pipeline
uv run pytest tests/engine/strategy_load_test.py tests/api/strategy_store_test.py
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
