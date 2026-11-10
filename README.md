# SeeAlpha

SeeAlpha 是基于 NautilusTrader 的多资产量化交易系统，面向加密资产、期货、股票等市场，
覆盖现货、永续合约、期权等交易产品，围绕数据准备、行情、策略研究、回测、风控与交易执行构建。

本仓库使用 `nautilus-trader 2.0.0rc7` 候选版软件包作为底层引擎，
并不是上游 NautilusTrader 源代码仓库的副本。SeeAlpha 是产品名称，NautilusTrader 是底层引擎名称。


## 产品定位与长期边界（2026-11-09）

SeeAlpha = **量化交易能力内核（Engine）+ 量化研发与运营平台（Platform）**。
Engine 专注数据、回测、策略运行、风控和交易执行的正确性、性能与可靠性；
Platform（`api/` + `web/`）专注用户/租户/授权、策略研发管理、任务编排、
可视化和业务运营，实际量化计算由 Engine 承担。

**正在分阶段拆分仓库**：独立 `saoxia/seealpha-engine` 已建立。旧 `engine/` 暂时保留供历史测试和安全回滚；新的开发/部署入口使用独立 Engine 服务或其固定版本镜像，不再从原仓库构建。
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

新业务端依赖已单独放在 [`platform/`](platform/README.md)，运行时不安装 NautilusTrader、NumPy、Pandas 或 PyArrow；旧根目录依赖暂供历史联合测试使用。可运行 `./scripts/check_platform_runtime.sh` 验证源码隔离与独立依赖环境。

## Web 管理端

先启动 API，再启动 Next.js 前端，浏览器打开
[综合管理端](http://127.0.0.1:3000) 的 RESEARCH 页，即可查看项目检查、
查看任务日志及历史报告。研究回测目录已删除，页面不再列出那些策略。使用现有本地数据，
登录沿用 API 用户会话。详见 [前端说明](web/README.md) 与 [API 说明](api/README.md)。

## 当前进度与检查

[项目状态及待办](PROJECT_STATUS.md) · [Web 管理端说明](web/README.md)

2026-11-09：Engine / Platform 第 8–14 项已合并至 `main`，第 14 项 [PR #38](https://github.com/saoxia/SeeAlpha/pull/38) 的 CI（Python、Docker、全量检查）为成功。该证据仅对应其提交版本；没有使用真实账户或执行真实交易。

旧的 BTC/ETH 研究策略和 2026-09 的几轮优化记录已随 `strategy_python/` 删除，不再作为可运行来源。旧机器的前向观察模块及专用运维工具已于 2026-10-27 移除。

本机开发检查使用：

```bash
python -m pytest -q
```

pytest 包含历史源码与授权更新核验；代码规范另用 `python -m ruff check .`。
Web 项目检查按同一范围依次运行 Ruff 和 pytest。

## 连续分钟行情层

以下行情和历史管道 CLI 在独立 `seealpha-engine` 仓库中运行；此处保留旧代码说明供迁移期间核对，Platform 运行不应启动这些进程。


新增独立的[统一行情层](engine/feed/README.md)，以已闭合 1 分钟 OHLCV 衔接本地历史、
REST 补齐与 WebSocket 实时数据，包含连续检查点、重启回放、来源冲突检测和数据就绪控制。
仅采集公开行情，尚未接入真实账户或自动启用现行策略。

```bash
.venv/bin/python -m engine.feed.service serve
.venv/bin/python -m engine.feed.service status
```

常驻交易引擎与业务 API 分开启动。引擎默认 `127.0.0.1:8766`，API 用 `ENGINE_URL` 连接。
新的启动方式见 [独立 Engine 接入](docs/EXTERNAL_ENGINE.md) 和 [API](api/README.md)。

```bash
# 先在独立 seealpha-engine 项目启动 Engine（参见 docs/EXTERNAL_ENGINE.md）
COOKIE_SECURE=false ENGINE_DEPLOYMENT=single \
  ENGINE_URL=http://127.0.0.1:8766 ENGINE_TOKEN='<私有开发令牌>' \
  .venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

## 进入项目环境

```bash
source ./activate.sh
```

激活脚本会把 Python、虚拟环境、下载的依赖、缓存、工具和临时文件都保存在
本项目目录内。

## 常用命令

以下同步环境命令用于安装或明确需要重建环境时；日常检查使用上面的本地环境入口。

```bash
uv sync --locked
uv run python -c "import nautilus_trader; print(nautilus_trader.__version__)"
uv run pytest
uv run ruff check .
```

测试正在分离：业务端无 Engine 依赖的回归位于 `platform/tests/`；
迁移期的联合测试仍位于根目录 `tests/`。独立 Engine 仓库负责其
`tests/connectors/`、`tests/feed/`、`tests/pipeline/`，本仓库已删除这
30 个与独立 Engine 完全相同的测试副本。根目录 `uv run pytest`
不会覆盖独立 Platform/Engine 两套完整测试及显式 HTTP 集成验收。
测试按关键行为和影响组织，不要求每个 Python 文件都有对应测试文件。
策略包的写法见 [`packages/ema_crossover/README.md`](packages/ema_crossover/README.md)。

仅在需要时添加特定交易所的集成。密钥必须放在 `.env` 或其他已忽略的本地
文件中，禁止提交真实交易密钥。

## 策略包

策略是一份可解压的 zip，由业务端登记、按开发或正式用途授权，再下发到一个租户。
引擎不保存策略名字清单。示例是双均线：金叉公布满额多头，死叉公布空仓。
写法见 [`packages/ema_crossover/README.md`](packages/ema_crossover/README.md)。

## 同步 Binance 公开归档

默认数据集配置为 Binance USD-M 永续 `BTC-USDT`、`ETH-USDT`、`SOL-USDT` 的逐笔 `trades`。
批量准备命令会从同一份 trades 生成 Nautilus `TradeTick` 以及 1s、1m、5m、15m Bar：

```bash
.venv/bin/python -m engine.pipeline.prepare_data \
  --start 2026-08-01 \
  --end 2026-08-02
```

[`engine/pipeline/sync_binance_file.py`](engine/pipeline/sync_binance_file.py) 用于将
`data.binance.vision` 上指定的单个公开数据归档同步到项目。既可以传完整 URL，
也可以传以 `data/` 开头的站内路径：

```bash
.venv/bin/python -m engine.pipeline.sync_binance_file \
  data/futures/um/daily/trades/ETHUSDT/ETHUSDT-trades-2026-08-01.zip
```

数据默认写入 `data/raw/binance/` 并保留远端目录结构。脚本会下载配套的
`.CHECKSUM`、执行 SHA-256 校验、使用临时文件原子替换，并在网络中断后尝试
断点续传。重复执行时，已经通过校验的文件会被跳过。

可通过 `--output-dir` 指定其他数据根目录；只有在远端确实没有校验文件且能
接受完整性风险时，才应使用 `--skip-checksum`。查看全部参数：

```bash
.venv/bin/python -m engine.pipeline.sync_binance_file --help
```

批量同步、Nautilus 转换、统一 `prepare_data()` 入口和目录内所有文件的职责，
详见 [`engine/pipeline/README.md`](engine/pipeline/README.md)。

回测完成后打开结果目录中的 `report.html` 查看中文交互报告，无需联网。
报告包含常规统计、参数、账户/成交/持仓明细，以及做多、做空、合计三条累计净收益曲线。
曲线按完整持仓平仓时间计入含结算币种手续费的已实现收益；分批退出延后至全部平仓时计入，
未平仓持仓与浮盈亏不计入。支持悬停、图例开关、缩放、拖动、自选日期和“全部 / 近 6 个月”。
“近 6 个月”相对回测结束时间计算，筛选保留原始累计值，指标卡统计完整回测区间。

2026-10-27: [Native capability adoption and validation](docs/NATIVE_CAPABILITY_ADOPTION.md).

## 数据模块分工

| 目录 | 职责 |
|---|---|
| [`engine/pipeline/`](engine/pipeline/README.md) | 历史归档下载、转换、校验、特征与 Catalog 入库 |
| [`engine/`](engine/README.md) | 常驻交易引擎、连接器、连续行情与历史工作进程 |
| [`connectors/`](connectors/README.md) | 交易所原生适配器配置、桥接与必要扩展 |
| [`core/`](core/README.md) | 公共行情类型、命名规则和项目路径 |

连接器与公共定义不依赖行情服务和离线管道；行情服务仅在历史 Catalog 桥接处使用管道存储接口。

## 工程边界

`api/` 提供当前前端所需 API，`web/app/` 为独立 Next.js 前端。
旧业务后端、`:8001` 兼容后端和 `:8765` Python 管理端已移除；检查与回测任务由 API 启动。
启动方式见 [API 说明](api/README.md) 与 [前端说明](web/README.md)。

- `core/`：公共行情定义和逻辑路径。
- `api/`：鉴权、账户、操作记录、行情管理，以及检查、回测、报告和任务日志。
- `web/`：独立 Next.js 前端，通过同源 `/api` 访问上述接口。
- `tests/`：自动化行为测试、源码基线及历史研究来源校验。
- 配置继续位于所属模块，依赖与工具链由根目录声明文件管理。

不设置独立的 conf、cli、runtime、checks、benchmarks 或 scripts 目录。
前端与 API 的环境和构建步骤见 [开发环境说明](DEV_ENVIRONMENT.md)。
