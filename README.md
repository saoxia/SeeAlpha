# SeeAlpha

SeeAlpha 是基于 NautilusTrader 的多资产量化交易系统，面向加密资产、期货、股票等市场，
覆盖现货、永续合约、期权等交易产品，围绕数据准备、行情、策略研究、回测、风控与交易执行构建。

NautilusTrader 属于独立 [seealpha-engine](https://github.com/saoxia/seealpha-engine) 的运行依赖，
本仓库的 Platform Python 环境不安装交易引擎。


## 产品定位与长期边界（2026-11-09）

SeeAlpha = **量化交易能力内核（Engine）+ 量化研发与运营平台（Platform）**。
Engine 专注数据、回测、策略运行、风控和交易执行的正确性、性能与可靠性；
Platform（`api/` + `web/`）专注用户/租户/授权、策略研发管理、任务编排、
可视化和业务运营，实际量化计算由 Engine 承担。

**Phase 20 已提取 Engine 源码**：本仓库不再包含 `engine/`、旧 Engine Dockerfile 或根目录重型 Python 依赖。Engine 位于 [saoxia/seealpha-engine](https://github.com/saoxia/seealpha-engine)，Platform 仅以 HTTP v2 和固定版本镜像调用。原生测试由 Engine 仓库 CI 负责；历史联合 API 兼容测试在隔离 CI 环境中通过固定提交检出 Engine，不在 Platform 服务进程中运行。
职责归属、依赖规则、剩余解耦项与验收条件见
[Engine / Platform 产品与架构边界](docs/ENGINE_PLATFORM_BOUNDARIES.md)；
当前已完成 Platform 的源码导入隔离和 Engine 跨进程契约验收，但不意味着已完成实盘策略或真实账户验收。

## 产品范围与当前实现

多资产、多交易产品是 SeeAlpha 的产品定位；具体市场能否使用，取决于对应连接器、
合约定义、数据准备、账户与执行接入的实现及验收情况。当前实现范围如下：

| 能力 | 当前实现 |
| --- | --- |
| 数据准备与行情 | Binance USD-M 永续合约公开行情，默认 BTC、ETH、SOL；连续行情层输出闭合分钟 OHLCV |
| 策略 | 可下发的策略包。示例是双均线，只公布目标持仓 |
| Web 管理端 | 独立 Next.js 前端与 FastAPI；项目检查、BTC/ETH 策略回测、报告和任务日志在 RESEARCH 页 |
| 账户与手动交易 | 已实现 Binance 原生账户只读、USD-M 永续手动下单/撤单通路及交易页（管理员显式确认）；真实账户凭据、交易所网络和真实下单尚未验收，不能视为实盘可用 |
| 业务持久化 | 通过 ORM 支持 PostgreSQL/SQLite；本机已连接 PostgreSQL 并完成迁移 |
| 其他市场与交易产品 | 已有 Binance 期权行情、历史数据与适配器代码，尚未完成真实账户及实盘策略验收；股票、其他期货和现货等产品仍需单独接入与验收 |

现有 Binance USD-M 命名、USDT 计价和分钟行情约定仅适用于当前实现，
不能直接作为所有市场的通用规则。新增接入优先复用 NautilusTrader 原生适配器，
并按市场明确交易场所、合约标识、精度、交易时段、费用和保证金等约束。

## 本机开发（2026-10-26）

项目已迁移到 macOS Apple Silicon；完整性校验通过后进行了环境清理。
本机开发环境已安装并验收：Python 3.14.9、NautilusTrader 2.0.0rc7、Rust 1.99.1。
该日全量 1,345 项测试通过；未启动长期采集或后台服务。这是 10 月 26 日的历史记录，非当前 CI 结果。详见 [开发环境说明](DEV_ENVIRONMENT.md)。
v2 接口、数据格式和版本可用性限制见 [升级记录](docs/ENGINE_UPGRADE.md)。
安装与重建步骤见 [开发环境说明](DEV_ENVIRONMENT.md)。
下文研究进度和测试数量为旧机器历史记录，不能视为本机运行状态。

本机 `data` 已链接到外置 APFS 卷 `ssd1`，具体位置和使用要求见 [数据存储说明](DATA_STORAGE.md)。

## 独立 Platform Python 环境

Platform 的唯一业务 Python 依赖锁位于 [`platform/`](platform/README.md)，运行时不安装 NautilusTrader、NumPy、Pandas 或 PyArrow；根目录不再保留重型 Python 项目或 uv 锁。通过 `./scripts/check_platform_runtime.sh` 验证。Engine 独立仓库管理自己的 Python/Rust 锁文件。

## Web 管理端

先启动 API，再启动 Next.js 前端，浏览器打开
[综合管理端](http://127.0.0.1:3000) 的 RESEARCH 页，即可查看项目检查、
查看任务日志及历史报告。研究回测目录已删除，页面不再列出那些策略。使用现有本地数据，
登录沿用 API 用户会话。详见 [前端说明](web/README.md) 与 [API 说明](api/README.md)。

## 当前进度与检查

[项目状态及待办](PROJECT_STATUS.md) · [Web 管理端说明](web/README.md)

2026-11-09：Engine / Platform 第 8–14 项已合并至 `main`，第 14 项 [PR #38](https://github.com/saoxia/SeeAlpha/pull/38) 的 CI（Python、Docker、全量检查）为成功。该证据仅对应其提交版本；没有使用真实账户或执行真实交易。

旧的 BTC/ETH 研究策略和 2026-09 的几轮优化记录已随 `strategy_python/` 删除，不再作为可运行来源。旧机器的前向观察模块及专用运维工具已于 2026-10-27 移除。

Platform 日常检查使用：

```bash
uv sync --project platform --locked
platform/.venv/bin/python -m pytest -c platform/pyproject.toml -q platform/tests
platform/.venv/bin/ruff check .
```

涉及独立 Engine 的兼容回归由 GitHub Actions 固定版本检出，使用独立 Engine 及 Platform 的两个锁文件，不需要在本机安装到业务 Python 环境。
Web 项目检查按同一范围依次运行 Ruff 和 pytest。

## Engine 与 Platform 的开发入口

常驻行情、策略和回测运行于独立 Engine 仓库，服务默认监听本地 8766。
Engine 的 [Feed](https://github.com/saoxia/seealpha-engine/blob/main/engine/feed/README.md)、
[历史数据管道](https://github.com/saoxia/seealpha-engine/blob/main/engine/pipeline/README.md)
以及 Rust 转换器均只在独立仓库开发或编译。

在 SeeAlpha Platform 仓库启动业务 API：

```bash
uv sync --project platform --locked --no-default-groups
export ENGINE_DEPLOYMENT=single
export ENGINE_URL=http://127.0.0.1:8766
export ENGINE_TOKEN='<独立 Engine 的私有令牌>'
export DATABASE_URL='<隔离且已迁移的业务数据库连接串>'
platform/.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

已有 Engine Docker 容器应长驻复用；普通回测不创建新的容器。部署与可选隔离
验收见 [外部 Engine 说明](docs/EXTERNAL_ENGINE.md)。

## 策略包

策略是一份可解压的 zip，由业务端登记、按开发或正式用途授权，再下发到一个租户。
引擎不保存策略名字清单。示例是双均线：金叉公布满额多头，死叉公布空仓。
写法见 [`packages/ema_crossover/README.md`](packages/ema_crossover/README.md)。

## 行情归档、转换与回测

公开归档下载、NautilusTradeTick/Bar 转换、Catalog 入库、连续行情进程以及
回测执行全部归独立 [seealpha-engine](https://github.com/saoxia/seealpha-engine)。
命令需在该仓库的独立运行环境执行，不能在 SeeAlpha Platform 根目录使用
`python -m engine...`。可参考
[Pipeline 手册](https://github.com/saoxia/seealpha-engine/blob/main/engine/pipeline/README.md)。
Platform 的研究任务仅经受控 HTTP 调用 Engine。

## 数据模块分工

| 组件 | 责任 |
|---|---|
| [seealpha-engine](https://github.com/saoxia/seealpha-engine) | Binance 连接器、行情 Feed、Pipeline、NautilusTrader 回测与订单执行 |
| `core/` | 两仓库同步的 HTTP 与交易契约、公共市场定义 |
| `api/` | 租户、账户、策略授权、业务操作账本、研究任务和 Engine HTTP 客户端 |
| `web/` | Next.js 前端 |
| `platform/` | 精简 Python 依赖锁、无 Engine 的 Platform 回归 |

独立 Engine 版本由跨仓库验收显式固定，不再通过 Platform 仓库目录决定。

## 工程边界

`api/` 提供当前前端所需 API，`web/app/` 为独立 Next.js 前端。
旧业务后端、`:8001` 兼容后端和 `:8765` Python 管理端已移除；检查与回测任务由 API 启动。
启动方式见 [API 说明](api/README.md) 与 [前端说明](web/README.md)。

- `core/`：公共行情定义和逻辑路径。
- `api/`：鉴权、账户、操作记录、行情管理，以及检查、回测、报告和任务日志。
- `web/`：独立 Next.js 前端，通过同源 `/api` 访问上述接口。
- `tests/`：自动化行为测试、源码基线及历史研究来源校验。
- 配置继续位于所属模块，Platform Python 依赖由 `platform/pyproject.toml` 与 `platform/uv.lock` 管理；独立 Engine 自行锁定其研究和 Rust 工具链。

不设置独立的 conf、cli、runtime、checks、benchmarks 或 scripts 目录。
前端与 API 的环境和构建步骤见 [开发环境说明](DEV_ENVIRONMENT.md)。
