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

`platform/tests/engine_http_routing_test.py` 已将原 `tests/api/tenant_docker_test.py` 中不需要真实 Engine 的关键消费者检查提取到独立 Platform 测试套件：HTTP v2、错误版本拒绝、Engine 不可用时失败关闭、租户 Token 隔离和旧镜像禁止复用。这些用例使用 HTTPX MockTransport 和临时目录，不依赖 NautilusTrader、原 `engine/` 或 Docker daemon。原测试保留，直到旧测试夹具完成跨进程迁移并集中验收。

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
