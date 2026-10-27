# 当前实际架构与冻结边界

核对日期：2026-10-27。本文记录当前工作区实现（含已有未提交文件），不构成迁移计划或新架构提案。开发约束见根目录 [AGENTS.md](../AGENTS.md)；具体模块规则继续查阅各目录 README。

## 技术栈与能力范围

SeeAlpha 是基于 NautilusTrader 的多资产量化交易项目，当前行情接入为 Binance USD-M 永续，研究与回测以 BTC/ETH 策略为主。根目录 `pyproject.toml`、`uv.lock` 管理 Python 及依赖，当前引擎固定为 `nautilus-trader==2.0.0rc7`；项目不是引擎源码仓库。

Python 承担行情、管道、策略、API 和兼容管理端；Rust 用于历史 trades 转换，保留 Python 回退。`strategy_rust/` 当前只有说明文件，不能视为已有可运行 Rust 策略。独立前端使用 Next.js、React、TypeScript 和 Tailwind，由 `web/package.json` 及锁文件管理。FastAPI 提供 API，SQLAlchemy/Alembic 管理业务持久化，支持 PostgreSQL/SQLite；ClickHouse 用于现有 OLAP 查询与监控。

现有账户与操作历史接入是只读查询，不等于已上线实盘下单；资产 K 线接口当前返回示例数据。不能将多资产产品定位或历史文档中的后续设计视为已实现能力。

## 核心模块与依赖

| 模块 | 当前职责与主要依赖 |
| --- | --- |
| `core/` | `market/models.py` 定义 `MinuteBar`、时间单位与 `DataError`；`market/instruments.py` 定义现有市场命名；`paths.py` 定位项目根目录。不反向依赖业务模块。 |
| `connectors/binance/` | 原生适配器接入与生命周期；`native.py`、`transport.py` 提供公开行情，`queries.py` 查询品种及账户，`operations.py` 读取操作历史。依赖公共定义及 Nautilus 公共能力，不依赖上层服务或数据库。 |
| `pipeline/` | `prepare_data.py` 统一协调归档下载、校验、转换、特征和 Catalog 入库。`catalog.py` 在 Nautilus v2 公共存储能力上提供项目存储接口；Rust 转换通过 Python 桥接调用。 |
| `feed/` | `service.py` 编排校时、历史衔接和修复；`feed.py` 控制就绪与回放交付，`store.py` 持久化连续分钟线。依赖 `connectors`、`core`，仅 `history.py` 通过 `pipeline.catalog` 桥接已有历史，不启动离线下载/重建。 |
| `strategy_python/` | 策略包及共享指标、值对象、风险和执行组件；`_backtesting.py` 组装引擎与数据，`portfolio.py` 按清单组装策略，`portfolio_backtest.py` 启动共用账户回测，`_report/` 输出报告。 |
| `api/`、`migrations/` | `main.py` 组装鉴权、交易所账户、操作账本及 OLAP 服务；服务通过 connector 查询，通过 repository/ORM 持久化，Alembic 管理业务表结构。启动时运行操作同步及 OLAP 缓存刷新，不启动实盘策略。 |
| `web/app/` | 独立 Next.js 界面，通过同源 `/api` 与 FastAPI 交互；代理规则在 `web/next.config.ts`，契约类型位于 `web/app/api/generated/schema.ts`。 |
| `web/*.py`、`web/static/` | 兼容 Python HTTP 管理端及独立页面；`server.py` 负责访问控制，`jobs.py` 执行固定检查/回测任务，`artifacts.py` 检索报告，`storage.py` 管理运行路径与迁移。与新前端/API 的登录机制独立。 |
| `tests/` | 按业务模块验证行为，包含策略依赖边界测试、冻结源码及研究来源核验。历史快照不等同于当前可运行入口。 |

## 公共入口、契约与主要数据流

### 离线数据与回测

```text
DataRequest → pipeline.prepare_data.prepare_data() → PreparedDataset
  Binance 日 trades ZIP/CHECKSUM → 校验 → Rust 转换（Python 回退）
  → TradeTick / 多周期 Bar → ParquetDataCatalog → 特征 Parquet

已有 Catalog → 单策略 backtest.py / portfolio_backtest.py
  → _backtesting.py → Nautilus 引擎 + Strategy → CSV/JSON/HTML 报告
```

回测默认只读取已有 Catalog；显式 `--check-data` 才准备缺失数据，`--force` 与 `--refresh-archives` 仍需该开关。默认补缺失，不因版本或配置变化自动重建；不得在策略信号或执行模块直接下载、修复数据。报告位于 `data/reports/backtest/<策略>/<run_id>/`，组合位于其 `portfolio/` 子目录。

### 连续分钟行情

```text
已有 Catalog ─→ CatalogHistory ─┐
Binance 原生 REST / WebSocket ─┴→ Coordinator / Store
  → 连续性、重叠校验、校时与新鲜度检查 → Feed 就绪门禁
  → Delivery(bar, allow_new_risk, reason) → 消费方
```

`MinuteBar` 使用规范化交易对、UTC 分钟开盘毫秒及 Decimal OHLCV；唯一键为交易对与开盘时间。转 Nautilus Bar 时事件时间为闭合边界前 1ms，以纳秒存储。回放和补洞交付不得获得实时决策资格；断线、冲突、缺口、时钟或消费者故障触发现有降级/恢复约束。连续服务默认数据目录为 `data/market_data/binance_um_1m/`，不回写历史 Catalog；当前未自动接入研究策略或实盘执行。

### 策略内部

```text
Market Data → Indicators / Features → Signal → Position Target
  → Risk Overlay → Final Target → Execution → Order
```

`strategy.py` 保持生命周期和事件编排；信号不访问订单，执行不判断指标，风险不产生 Alpha。配置与运行状态分离；具体策略不互相导入，共享组件不依赖具体策略。组合层可以组装具体策略，策略不反向依赖组合与回测入口。公共回测入口包括 `StrategyBacktest`、`run_backtest_cli`；兼容导出按根目录规范核实真实调用和支持承诺后处理，不得破坏公共接口。具体约束见 [策略工程约定](../strategy_python/README.md) 和 `tests/strategy_python/architecture_test.py`。

### 管理端、账户与账本

```text
浏览器 → Next.js 同源 /api 代理 → FastAPI 路由 / 鉴权
  → 账户 / 操作记录服务 → Binance connector → 原生查询节点
  → repository / ORM → 业务数据库
  → OLAP 服务 → ClickHouse 查询 / 监控缓存

兼容 Python 管理端 → 固定任务子进程 → 检查 / 策略回测
  → 任务记录、文件日志和报告索引
```

API 核心路径为 `/api/health`、`/api/auth/*`、`/api/system/exchange-accounts/*`、`/api/exchange-accounts/*`、`/api/operations/*`、`/api/system/olap/*` 与 `/api/clickhouse/*`；字段以路由、contracts/models 和 OpenAPI 为准。`/api/system/update` 在前端返回 410。浏览器不直接访问数据库、Python 模块或本地业务文件。

操作账本保存观察证据与合并投影，区分项目、外部及对账修正，保留去重、归属冲突拒绝和事务语义。`OperationService.record_project_event()` 是已有项目事件持久化接入点，不代表当前已经运行实盘节点；查询节点不能替代完整历史审计。详见 [操作记录 API](OPERATIONS_API.md)。

## 持久化边界

- `data/` 保存原始归档、Catalog、特征、连续行情和报告；当前链接外置盘，使用规则见 [DATA_STORAGE.md](../DATA_STORAGE.md)。磁盘不可用不能当作空数据并自动重建或清除记录。
- 业务数据库保存用户/会话、加密账户配置、操作账本和 OLAP 设置/缓存；启用 ORM 的兼容管理端还保存任务及报告索引。正式 API 要求显式数据库连接并检查 Alembic 版本，不在连接失败时切换数据库。
- 兼容管理端未启用 ORM 时保留本地任务 JSON；`.local/` 保存私有配置、运行状态及工具环境，`logs/` 保存日志。报告原文件和任务日志仍在文件系统，业务库不能替代其备份。
- Catalog、ClickHouse 和 Nautilus 引擎状态各自管理，不能混作业务 ORM 表或相互替代。路径以项目位置解析，配置继续属于各模块。

## 禁止擅自跨越的边界

主技术栈与引擎版本、上述模块职责/依赖方向、策略信号至执行数据流、公共 CLI/HTTP/数据契约、存储归属与双管理端兼容关系均冻结。不得擅自合并服务、迁移存储、建立新架构层、替换交易引擎/适配器、改写历史研究来源或启用实盘执行。涉及这些事项只记录建议及影响，待用户明确另行授权后才可实施；现有文档中的未来设想不构成授权。
