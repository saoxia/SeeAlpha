
## 2026-11-09：每租户 Docker Engine（可选部署）

本轮用户明确授权将 Engine 按租户部署为独立 Docker 容器并由业务后台路由管理，
开发租户使用独立历史数据和持久卷，不实施数据缓存复用。
`api/tenant_docker.py` 在同宿主机上创建/停止带资源限制的 Engine 容器，
`api/engine_client.py` 按租户选择私有 HTTP 端点；`engine/app.py` 限制固定租户身份。
全部租户（包括初始正式租户）均使用独立数据卷，不再绑定旧项目数据目录。
`ENGINE_DEPLOYMENT=single` 仍为默认；改用 Docker 需全新初始化，旧 Engine 状态不自动搬迁。
详情与部署前必要迁移见 [运行说明](TENANT_DOCKER_RUNTIME.md)。
跨服务器调度、Harness、共享缓存与不可信代码沙箱均不属于本阶段。

# 当前实际架构与冻结边界

2026-11-08 用户明确授权 Phase 1 架构拆分，并在随后授权把剩余数据管道一并移入引擎。
常驻交易引擎在 `engine/`：`engine/feed/` 是原行情树，`engine/connectors/` 是原连接器，
`engine/pipeline/` 是完整数据管道（历史工作进程、归档下载、Rust 转换、Catalog、
`pair_repair`、`options_data`、`pair_deletion` 与 `data_management`）。仓库根目录不再保留
`pipeline` 包。`api/` 仍是业务后端（用户、会话、加密账户库、账本、资产视图和 `/api/*` BFF）。
`web/` 未移动。2026-11-08 用户随后授权删除 `strategy_python/`。一次性离线任务由引擎启动 `engine.pipeline.data_management`；
业务库仍保存订阅意图和任务索引。

引擎进程 `python -m engine` 默认监听 `127.0.0.1:8766`。业务 API 用 `ENGINE_URL`
（默认 `http://127.0.0.1:8766`）和 `ENGINE_TOKEN` 调用它。引擎本地 SQLite（WAL）
在 `.local/state/engine/book.sqlite`，只记录连接状态、订单请求幂等、订单、成交、持仓、
缺口、运行期订阅、已下发策略和事件。主密钥与解密后的交易所密钥不入库；API 按账户把凭据下发给引擎。
公开行情订阅意图仍在业务表 `market_data_policies`。账户查询、手动交易和估值用的临时
LiveNode 由引擎执行，不再由 API 进程构造。操作账本同步仍从 API 调用连接器的只读签名
接口，资产校准的只读 REST 也仍在 API；二者都不是账户/手动/估值 LiveNode。
策略商店在业务后端登记可加载的包和版本，并记录该版本可以加载到哪一类租户。
开发授权和正式授权是两条记录，正式授权默认没有。业务端授权后按交易所密钥的同一下发方式
把包交给引擎；引擎只接收已下发的包，并用 `tenant_id` 隔离。包是带
`seealpha-package.json` 的 zip，入口由归档自己声明，引擎不再按策略名字做允许清单。
示例包是 `packages/ema_crossover/`：单品种 EMA 金叉公布满额多头，死叉公布空仓。
策略只公布目标持仓。引擎用 `PortfolioCoordinator` 与 `target_netting`：先按账户风险额度
截断合计目标，再对当前净持仓做目标轧差，差额仍走 Nautilus RiskEngine / ExecutionEngine。
预览使用模拟 venue，不读取密钥，也不发真实订单。
业务库中的租户与空模板已接入：正式租户可保存交易所密钥，开发租户只保存虚拟账户；
行情订阅意图、账户和引擎账本按租户隔离。模板不含品种、资金或密钥。
详见下文「租户」和「策略商店」。

2026-11-08 用户明确要求期权历史继续使用固定公开 `/eapi/v1/klines`，并将实时盘口和
Tick 同时持久化。沿既有期权批量 connector → Feed → history 桥 → Pipeline 路径，
管理采集默认订阅真实 Tick、前 10 档盘口快照及原有闭合 K 线；Tick 写原 Catalog，
盘口以 SHA256 JSON 原始来源保存到原 raw 边界，包含交易所更新编号/时间及接收时间。
不新增服务、业务表或派生 Catalog 类型，不改变交易风险资格；盘口只保留实际收到的快照，
断线历史不可回溯部分保留缺口。手动历史下载仅复用完成整日分页的 REST 来源，
实时/恢复片段不再导致整日跳过；旧来源保留，不自动重建已有行情。

2026-11-08 用户明确要求自动历史核验改为比较已保存 K 线，并对一致或归档修复后复核通过
的日期做完成标记，不再周期复扫。沿原 Pipeline 核验、来源修复、Feed 计算通知及逐日审计
文件实现；K 线 OHLCV 核验不替代独立 Tick 身份/笔数全量校验。完成标记绑定配置与核验
版本，待计算或冲突日期不提前完成，不增加后台服务、业务数据库表或新的存储边界。

2026-11-08 用户明确授权永续行情增加长期保存、公开展示的本地 1s，并将原分钟驱动的
高周期改为秒线直接驱动：Tick → 1s → 所有更高周期。沿既有 PairStore、Pipeline 历史
派生及 Catalog/SQLite 边界实现；官方 15 个周期清单不变，管理配置另包含本地 1s。
仅保存 K 线时，历史 Tick 工作来源沿用 `history/source_catalog`，不暴露为保留 Tick。
不把官方分钟线拆成秒线，不改期权、原分钟 Feed/Delivery、交易引擎或策略门禁；
不新增服务或数据库迁移，不自动重建、替换已有闭合数据。

2026-10-31 用户明确要求接入资金变动：在既有 API 操作服务 → Binance connector → ORM
账本职责内增加 USD-M 只读收支历史，扩展操作事件为 `cashflow`；原生余额、仓位和订单路径保留。
锁定 Python 公共接口未暴露该历史，使用固定官方签名 GET，不复制引擎或访问上游私有接口。
记录复用 `ledger_operations` / `ledger_observations`，覆盖和恢复检查点复用 `ledger_sync` JSON，
后台复用原同步循环，无新增数据库表、后台服务或存储边界变化。已有数据库的事件类型检查
约束需要单独授权应用 `3debb89b484a` 后才能保存资金事件；模型同步声明该已有约束。
本机随后已获明确授权并完成该迁移，核实原有证据未改写后重启 API，资金流水开始入库。
本轮不实现快照倒推、余额/权益计算或交易，现货钱包和其他产品资金来源未扩展。
详见 [资金流水](OPERATIONS_API.md#usd-m-资金流水2026-10-31)。

核对日期：2026-10-27。本文记录当前工作区实现（含已有未提交文件），不构成迁移计划或新架构提案。开发约束见根目录 [AGENTS.md](../AGENTS.md)；具体模块规则继续查阅各目录 README。

## 技术栈与能力范围

SeeAlpha 是基于 NautilusTrader 的多资产量化交易项目，当前行情接入为 Binance USD-M 永续，研究与回测以 BTC/ETH 策略为主。根目录 `pyproject.toml`、`uv.lock` 管理 Python 及依赖，当前引擎固定为 `nautilus-trader==2.0.0rc7`；项目不是引擎源码仓库。

Python 承担行情、管道、策略和 API；Rust 用于历史 trades 转换，保留 Python 回退。`strategy_rust/` 当前只有说明文件，不能视为已有可运行 Rust 策略。独立前端使用 Next.js、React、TypeScript 和 Tailwind，由 `web/package.json` 及锁文件管理。FastAPI 提供 API，SQLAlchemy/Alembic 管理业务持久化，支持 PostgreSQL/SQLite；ClickHouse 用于现有 OLAP 查询与监控。

账户与操作历史查询保持只读；独立 TRADE 手动入口按显式确认发送交易命令，策略实盘未上线（范围见文末）。资产 K 线按启用后持久化的估值证据生成，缺口保留为空；部署须先完成已批准功能对应的迁移。不能将多资产产品定位或历史文档中的后续设计视为已实现能力。

## 核心模块与依赖

| 模块 | 当前职责与主要依赖 |
| --- | --- |
| `core/` | `market/models.py` 定义 `MinuteBar`、时间单位与 `DataError`；`market/instruments.py` 定义现有市场命名；`paths.py` 定位项目根目录。不反向依赖业务模块。 |
| `engine/` | 常驻交易引擎。内部 HTTP 供 `api/` 调用；SQLite 保存订单、成交、持仓、缺口、幂等请求和运行期订阅。启动/停止 `engine.feed.managed`、`engine.pipeline.pair_data` 与 `engine.pipeline.data_management`。账户、手动交易和估值 LiveNode 只在这里构建。 |
| `engine/connectors/binance/` | 原生适配器接入与生命周期；`native.py`、`transport.py` 提供公开行情，`queries.py` 查询品种及账户，`operations.py` 读取操作历史。依赖公共定义及 Nautilus 公共能力，不依赖 `engine.feed`、`engine.pipeline`、`api` 或 `web`。 |
| `engine/pipeline/` | `prepare_data.py` 统一协调归档下载、校验、转换、特征和 Catalog 入库。`catalog.py` 在 Nautilus v2 公共存储能力上提供项目存储接口；Rust 转换通过 Python 桥接调用。`pair_data` 是历史工作进程，`data_management` 是一次性离线任务。 |
| `engine/feed/` | `service.py` 编排校时、历史衔接和修复；`feed.py` 控制就绪与回放交付，`store.py` 持久化连续分钟线。依赖 `engine.connectors`、`core`，仅 `history.py` 通过 `engine.pipeline.catalog` 桥接已有历史，不启动离线下载/重建。 |
| `packages/` | 可登记的策略包。当前示例是 `ema_crossover/`：元数据、入口和只公布目标持仓的实现。引擎不内置策略名字清单。 |
| `api/`、`migrations/` | `main.py` 组装鉴权、交易所账户、操作账本、OLAP、离线数据管理、租户和策略商店，以及检查/回测任务。行情订阅意图、策略包授权和业务账本经 repository/ORM 持久化，Alembic 管理业务表结构。账户快照、手动订单、估值事件、行情采集、策略下发和一次性离线数据任务都改调引擎。研究任务调用既有检查与策略回测入口。启动时运行操作同步与 OLAP 缓存刷新；持有管理锁的进程恢复研究任务。策略商店不启动交易所实盘。 |
| `web/app/` | 独立 Next.js 界面，通过同源 `/api` 与 FastAPI 交互；代理规则在 `web/next.config.ts`，契约类型位于 `web/app/api/generated/schema.ts`。RESEARCH 页对应 `/api/management`。 |
| `tests/` | 按业务模块验证行为，包含策略依赖边界测试、冻结源码及研究来源核验。历史快照不等同于当前可运行入口。 |

## 公共入口、契约与主要数据流

### 离线数据与回测

```text
DataRequest → engine.pipeline.prepare_data.prepare_data() → PreparedDataset
  Binance 日 trades ZIP/CHECKSUM → 校验 → Rust 转换（Python 回退）
  → TradeTick / 多周期 Bar → ParquetDataCatalog → 特征 Parquet

已有 Catalog → 开发租户声明订阅意图 → 引擎只加载已下发给该租户的包
  → 虚拟账户资金 → PortfolioCoordinator 目标轧差 → 回测结果（成交 / 权益）
```

数据准备默认补缺失，不因版本或配置变化自动重建。策略包不直接下载或修复数据。

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
  → Risk Overlay → Final Target
  → 单策略 / independent：Execution → Order
  → 多成员净额组合：Coordinator 合并目标 → 只交易账户净差额
```

2026-11-06 用户明确授权第四代组合层。成员策略仍各自产生目标，不改写信号和指标。
组合协调器按合约把带符号目标净额合并，执行路径只发净目标与
`Portfolio.net_position` 的差额。Nautilus 的 RiskEngine 继续检查这张订单；
OMS 的 NETTING 只合并成交后的持仓，不能代替下单前的目标对冲。
单策略回测和清单 `execution=independent` 保持各策略自己下单。
已下发到引擎的策略不走下单入口：策略只公布目标持仓，引擎侧协调器合并后轧差。
指标和信号留在策略包内，不是平台对象。作者约定见
[双均线示例](../packages/ema_crossover/README.md)。
本轮不包含市场状态调权、FOF 动态再平衡、期权对冲或账户级最大回撤锁仓。
保护性止损和应急退出先记在协调器里，触发后再进入净目标，避免静默丢掉风险退出。

### 管理端、账户与账本

```text
浏览器 → Next.js 同源 /api 代理 → FastAPI 路由 / 鉴权
  → 账户 / 操作记录服务 → Binance connector → 原生查询节点
  → repository / ORM → 业务数据库
  → OLAP 服务 → ClickHouse 查询 / 监控缓存

FastAPI `/api/management` → 固定任务子进程 → 检查 / 策略回测
  → 任务记录、文件日志和报告索引
```

API 核心路径为 `/api/health`、`/api/auth/*`、`/api/system/exchange-accounts/*`、`/api/exchange-accounts/*`、`/api/operations/*`、`/api/system/olap/*`、`/api/clickhouse/*` 与 `/api/management/*`；字段以路由、contracts/models 和 OpenAPI 为准。`/api/system/update` 在前端返回 410。浏览器不直接访问数据库、Python 模块或本地业务文件。

2026-10-31 按用户明确要求完成前端/API 拆分第二阶段：移除 `:8765` Python 管理端和已不存在的 `:8001` 兼容后端。检查、回测、报告与任务日志改由现有 FastAPI 会话提供，任务仍写入原 ORM 表和原文件路径，不新增表、不改策略研究来源、不启用实盘。另一端口或额外 worker 未拿到 `.local/run/web/server.lock` 时继续提供其余 API 和管理查询；只有持锁进程执行任务，并在自己启动时把未完成任务标为中断。

2026-10-28 按授权增加 `/api/data/*`：浏览器经同源代理访问离线 Tick 预览、覆盖、
质量与存储状态，管理员可发起现有 Pipeline 的补缺失、只读逐笔校验、本地派生 K 线与导出。
任务由 API 启动固定子进程，元数据复用 ORM `ManagementJob`；结果在 `.local/state/api/data_jobs/`，
日志在 `logs/api/data/`，导出在 `data/exports/ticks/`。未改既有模块依赖方向，
当时未迁移检查/回测任务；该迁移已于 2026-10-31 按后续授权完成。不强制重建，不增加实时 Tick 采集或交易执行。
详见 [离线数据管理 API](DATA_MANAGEMENT_API.md)。

2026-10-28 按用户授权补充历史数据删除：沿用相同 API → Pipeline 固定任务及 ORM 任务表，
先预览完整 UTC 日的文件清单，再凭确认和文件指纹执行；默认保留归档，同步清理物化数据和依赖缓存。
不改既有 CLI 并发约定，不迁移数据库，不删除账户、连续行情或研究报告。

操作账本保存观察证据与合并投影，区分项目、外部及对账修正，保留去重、归属冲突拒绝和事务语义。`OperationService.record_project_event()` 是已有项目事件持久化接入点，不代表当前已经运行实盘节点；查询节点不能替代完整历史审计。详见 [操作记录 API](OPERATIONS_API.md)。

## 持久化边界

- `data/` 保存原始归档、Catalog、特征、连续行情和报告；当前链接外置盘，使用规则见 [DATA_STORAGE.md](../DATA_STORAGE.md)。磁盘不可用不能当作空数据并自动重建或清除记录。
- 业务数据库保存用户/会话、加密账户配置、操作账本、资产估值证据/派生曲线/重算审计、OLAP 设置/缓存，以及研究任务和回测报告索引。正式 API 要求显式数据库连接并检查 Alembic 版本，不在连接失败时切换数据库。
- 未启用 ORM 时研究任务仍可落在本地 JSON；`.local/` 保存私有配置、运行状态及工具环境，`logs/` 保存日志。报告原文件和任务日志仍在文件系统，业务库不能替代其备份。
- Catalog、ClickHouse 和 Nautilus 引擎状态各自管理，不能混作业务 ORM 表或相互替代。路径以项目位置解析，配置继续属于各模块。

## 禁止擅自跨越的边界

主技术栈与引擎版本、上述模块职责/依赖方向、策略信号至执行数据流、公共 CLI/HTTP/数据契约与存储归属均冻结。不得擅自合并服务、迁移存储、建立新架构层、替换交易引擎/适配器、改写历史研究来源或启用实盘执行。涉及这些事项只记录建议及影响，待用户明确另行授权后才可实施；现有文档中的未来设想不构成授权。2026-10-31 的管理端合并、2026-11-08 的 `engine/` 拆分，以及同日把完整数据管道移入 `engine/pipeline/`，属于该项明确授权。同日稍后授权删除 `strategy_python/`，改为可下发的策略包。这不构成搬迁 `web/` 或启用策略实盘的许可。


2026-10-28 按用户授权，行情数据管理取消预设交易对：复用既有 Binance 品种查询、
API → Pipeline 任务调用和 Catalog 存储，从已接入的 USDT 永续品种中选择，
以单次任务设置传入用户选择的品种；页面库存按实际数据/订阅/活动任务生成。
不修改研究默认 TOML、冻结源码或其他 CLI，不增加交易市场、账户或执行能力。


2026-10-28 按用户授权，USD-M 行情管理解除 USDT 限制，支持 USDC 命名、品种查询及库存。
沿用既有归档/转换/存储与账户权限；不改变账户估值或原生交易引擎。
期权随后获得用户明确授权，扩展受冻结保护的产品模型并登记来源更新，实施说明见
[期权数据接入方案](OPTIONS_DATA_PLAN.md)。


2026-10-28 用户明确批准期权接入：connectors 补充固定的 EAPI 公开只读端点，
API/现有任务编排 → options_data → native CryptoOption/TradeTick/Bar → 既有 Catalog。
来源快照与原始行情同属 raw 存储，产品参与请求、库存和删除指纹；
不修改引擎、实时 Feed 或交易执行，不迁移数据库，不重写历史研究归档。

2026-10-29 用户明确要求制作 Binance 期权适配器，并确认“行情与账户：资产、持仓和订单查询”。
在既有 connectors 职责内增加公开 Python MarketDataClient/config/factory，
通过原生节点标准请求与订阅交付期权 TradeTick、QuoteTick、OrderBookDepth、Bar；
账户查询独立使用白名单只读签名 GET，不注册执行客户端、不接通任何交易命令。
行情解析由 connector 提供，离线 pipeline 复用；依赖方向保持不变。
不自动启动期权 Feed/策略、不改前端/API 契约、不迁移或重写存储、不修改冻结源码。
详见 [期权适配器](BINANCE_OPTIONS_ADAPTER.md)。

2026-10-29 用户另行明确授权增加期权下单、撤单。在既有 connector 内新增公开
ExecutionClient/config/factory 与固定 EAPI 写端点，原生 RiskEngine 和 ExecutionEngine
继续拥有风险与订单状态；connector 仅发送命令、核对证据并交付原生账户/订单/成交报告。
读写客户端分离，复用签名发送，超时不盲目重发，来源不完整时禁止新增风险。
不改账户估值、API/前端契约、现有策略、引擎版本或持久化边界，不自动注册或启动实盘节点；
验收仅用虚构凭据和模拟交易所，没有执行真实交易。

2026-10-29 按用户要求取消历史行情管理的 31 天跨度限制：前端选择、HTTP DataRange 和
Pipeline 管理入口一致接受跨月、跨年范围，复用现有逐日处理。能力字段保留，null 表示
不设固定跨度上限；日期顺序、时区、闭合 UTC 日、预览窗口、任务互斥/取消/运行超时、
删除快照与确认保护保持不变。不自动提交真实下载，不改冻结模型或存储边界。

2026-10-29 用户明确要求参照交接包的采集/补缺/聚合思路实现，明确不迁移行情至
ClickHouse。本轮新增管理 API → 固定 `engine.feed.managed` 子进程的分钟采集启停；
复用现有 Feed 校时、历史桥接、REST 补缺、连续门禁和 SQLite 存储职责。
每品种目录位于原 Feed 存储下的 managed/；派生闭合分钟周期写同目录 rollups.sqlite，
形成中更新使用独立标记和回调，不改变原有闭合 MinuteBar / Delivery 语义。
配置存于 API 本地状态，复用鉴权、来源检查和数据操作排他机制，不迁移业务数据库。
Catalog、ClickHouse、Connector 依赖边界、引擎及策略执行保持不变。
当前实时范围为 USD-M 1m 基础与 5m/15m 派生；Tick/1s/期权连续采集、仅保留选定
基础级别、Tick 脏范围队列及全历史自动修正尚未实现，见数据管理 API 的范围说明。

2026-10-29 用户确认取消聚合来源配置，改由后台自动决定。前端仅保存所需周期和实时开关；
API 的 base_level 默认 auto，旧字段值保留兼容，不重写已有配置。历史派生继续使用既有
冻结转换入口的来源选择；Feed 优先复用完整闭合低周期，其余时间段使用源分钟与实时尾部，
发布 source_levels 供查看，保持无重叠、完整性标记与原有就绪门禁。
不改变存储归属、策略数据契约、引擎或冻结源码；连续 Tick 落盘与补缺仍未实现。

2026-10-29 用户明确授权按交易对最小保存粒度、可选历史追加与默认实时更新，
并允许相关契约及必要源码扩展。新配置经原订阅管理启动固定 `engine.feed.managed --policy`，
管理日志仍属原 Feed 存储，新历史 `engine.pipeline.pair_data` 仍写原 Catalog，复用 ORM 任务表。
core 增加日历边界，connectors 补充既有公开 HTTP 白名单；Feed 通过原 history 桥只读
补回已存在的 raw trades，不启动离线下载。原分钟 Feed/Delivery 风险门禁不变。
原生 aggTrade 不替代真实逐笔；近期来源不足时保留缺口，所有新管理更新禁止新增风险。
旧 auto 分钟配置兼容，不迁移已有数据；期权连续采集及完整历史逐笔仍未覆盖。
共享 DataRequest 月线命名变更按用户授权登记，冻结研究快照和历史清单保持原字节。
详见 [当前数据管理范围](DATA_MANAGEMENT_API.md#按交易对配置与分钟实时管理2026-10-29)。


2026-10-29 用户明确调整顺序：先实现实时接入及数据变化触发的 K 线，后做历史回溯。
保留 `engine.feed.managed --policy` 与 `engine.pipeline.pair_data` 两个独立进程、现有 SQLite / Catalog 存储边界；
connector 补充原生 aggTrade 门面无法提供的固定原始逐笔 WebSocket，不替换引擎或修改原分钟 Feed。
PairStore 在源写入事务中登记待办，由真实源变化触发 1s、1m 和更高周期的受影响范围重算，
确认、去重、工作缓存、迟到数据和恢复均由 Feed 承担，不新增架构层或业务数据库迁移。
历史下载与 ClickHouse 数据归属不变，不自动重试失败任务，不启动交易。


2026-10-29 用户确认重新评估全局数据锁：在既有 DataService / Subscriptions 职责内改为
产品与交易对级 job 排他、access 共享 / 删除独占许可。不同交易对任务可并行，实时采集
与历史任务可共享访问；同一交易对任务仍串行，以覆盖日归档、跨日周期和指标缓存的实际冲突。
子进程继承锁，恢复与前端阻塞均按交易对处理；仅历史参数变化不重启采集。
进程托管、存储边界、HTTP 契约、冻结转换入口及手动 CLI 并发约定保持不变。


2026-10-29 用户明确授权行情管理转为配置驱动：数据库仅存 `market_data_policies` 参数，
实时接入、历史补齐、K 线计算为 API 托管的三个进程；监控为 API 内定期运行的第四个服务。
服务可重启，其恢复水位、缺口、积压、待办及监控快照全部由文件持有，不写业务任务表。
历史写源后发布文件变化通知，计算经原 Feed 历史桥调用 Pipeline；实时计算独立消费
同一 SQLite 的持久待办。既有 Catalog / Feed 存储、公共交易门禁、引擎及依赖方向保留。
旧一次性 HTTP / CLI 操作保留兼容，状态改存文件，原数据库审计不自动删除或迁移。
用户随后明确授权迁移、导入与旧行情清理；本机已完成配置表迁移，保留有效参数后移除旧 JSON、
旧行情任务和运行数据，从新服务重新采集。账户、账本、研究报告及冻结源码保持不变。
细节见[四个服务与配置切换](DATA_MANAGEMENT_API.md#四个服务与配置切换2026-10-29)。


2026-10-30 用户明确要求忽略逐笔成交编号跳号，不检查编号连续性。管理端 PairStore
继续按编号去重与校验同编号内容冲突，但不再登记或修复编号缺口，也不以旧编号缺口
判定实时健康或派生完整性。旧记录与 schema 保留，不执行数据清理或迁移；
状态 gap_count 保留兼容并返回 0。零值事件正常过滤；断线、过期、校时、数值和身份
冲突检查、原分钟 Feed 连续门禁、K 线闭合边界及所有管理更新禁止新增风险保持不变。


2026-10-30 按用户要求，管理端历史补齐的全部历史、指定起点和追加 N 天在保存时统一
解析为固定的 UTC 零点开始日期，复用原配置参数 `history_start`，模式统一为 `start`。
服务按该日期和独立文件检查点逐日处理；无数据库迁移，不改模块职责、存储边界或旧 CLI。
旧配置在下次启用历史并保存时解析，不批量重写已有参数或运行数据。


2026-10-30 用户明确要求参照部署包按大周期逐级定位历史差异后补齐，并取消自定义 K 线周期。
现有 `engine.pipeline.pair_data` 历史进程通过同模块职责下的 `pair_repair` 核验实际来源，
仅追加确认缺失的分钟或成交；逐日证据及巡检检查点仍属 managed 文件。
API 显式 USD-M 配置固定全部 15 个官方分钟至月线周期；实时由成交直接聚合分钟，
保留原完整性与风险门禁，秒线仅保留既有数据及兼容入口。不改变 Catalog / Feed / 业务库
归属，不引入 ClickHouse 行情存储、新服务、交易能力或数据库迁移，不改冻结转换器。


2026-10-30 用户要求统一在交易对配置调整数据开始日期，并明确确认调晚时删除此前行情、
缓存和原始归档。API 在既有管理职责内增加配置计划及绑定文件/参数的确认；
日期调早由原历史服务自动补缺，调晚沿原固定 Pipeline 子进程执行前缀裁剪。
成功后保存新配置并恢复服务，失败保持旧日期与暂停状态。跨界文件按记录原子裁剪，
不改变 Catalog / Feed / 业务库归属、不新增服务或迁移、不修改引擎、冻结来源或交易门禁。


2026-11-06 用户明确要求新增 Binance USD-M 永续手动交易页，位于 LIVE 与 DATA 之间。
在既有浏览器 → API → connector → Nautilus 的依赖方向内增加 `/api/trading/*`，
复用加密账户、会话/来源检查、原生风控和执行引擎，显式提交限价、市价及撤单。
只读节点与手动命令入口分开，共享原有节点配置；不启动实盘策略、不自动下单，
不改变已有行情/策略数据流，不更换引擎或迁移数据库。幂等及命令恢复证据由 API 本地状态文件保存，
成交与订单证据复用原账本；不删除已有数据。范围及未验收项见 [手动交易](MANUAL_TRADING.md)。

手动页按用户要求自动沿用账户持仓模式，移除持仓方向选择。connector 在既有职责内
用固定官方只读 GET 补充锁定公共 Python 节点未发布的模式及显示用风险价格，
不写账户设置、不替换原生风控与执行；仅展示匹配原生持仓的交易所风险价格。


2026-11-07 用户明确要求交易页默认由前端直接订阅 Binance，并允许切换至已维护的本地行情。
本轮仅扩展公开显示行情路径：浏览器 → Binance 公开 REST / WebSocket；
本地 K 线沿已有浏览器 → API 数据管理 → Catalog / Feed 只读投影，复用存储及共享读取锁，
不新增服务或存储层。切换只影响图表 K 线，不改账户、订单、原生风控报价或策略 Feed
就绪门禁；测试网不读取本地实盘 K 线。
用户随后明确授权清除旧行情 API：移除 `/api/trading/market` 及其 connector 快照实现与
专用 HTTP 类型；合约选择器 `/api/trading/markets` 保留。本地 K 线轮询按要求改为 1 秒。

2026-11-07 用户批准 LIVE 增加刷新资产、小额资产折叠及低频估值。
在既有 API 汇总服务 → connector 职责内，以固定只读账户 REST 补充原生 Python 快照
未发布的资产模式与分币种浮盈亏，以公开资产指数换算 USDT；portfolio 增加可选刷新参数
及资产明细。多资产模式优先使用交易所总额，单资产混币种钱包标记估值，不推算交易风险率。
缓存仍由 API 内存持有，不增加后台服务、数据库迁移或存储边界；原生余额/仓位接口与
手动交易、策略及风控路径保持不变。详见 [刷新资产](BINANCE_NATIVE_API.md#刷新资产与小额资产2026-11-07)。


2026-11-07 用户明确批准 USD-M 预估资产事件估值、混合校准和一键重算。
沿既有 API → connector 的只读账户 REST 与 Nautilus 公共行情取证，API → ORM 保存原始估值证据、
派生点、最新状态和重算任务；API 托管进程内采集，由文件锁保证同部署单写者。
账户 REST 每 5 秒检测余额与持仓变化，持仓订阅原生标记价并每 250ms 合并观察；
资金账本不二次累计进余额。资产公开行情节点不配置执行客户端、不触碰共享 listenKey，
避免既有短时账户查询关闭私有会话时打断资产采集。
每 300 秒独立查询对账，结构差异立即处理，其他超阈值差异连续两次校准。
原权益 K 线示例实现已替换，无证据/缺价/断线区间不补造。重算只替换派生点，保留原证据。
本次明确授权新增四张业务表和资产查询/重算 API；迁移文件准备后仍需明确授权应用到运行数据库。
不改行情 Catalog/Feed、账本职责、交易引擎或策略风控输入，不执行真实交易。
详见 [资产估值与重算](EQUITY_VALUATION.md)。


2026-11-07 用户要求数据页期权直接全选并自动纳入新增合约。沿用既有配置表、
API 配置恢复循环与 connector 公开目录查询，全选范围仅在行情管理配置中以
`options/ALL` 表示，新合约继承配置，已有及过期合约保留。具体行情与删除仍按合约处理；
用户随后明确要求同时接通持续采集和历史补齐：原 `engine.feed.managed` 复用期权 connector 的
公开传输与原生行情解析，原 `engine.pipeline.pair_data --service` 转入期权 K 线补齐。
源证据经既有 Feed history 桥由 Pipeline 写原 raw/Catalog，按合约文件锁、原子安装与
文件检查点恢复；不另起期权计算进程，不改存储边界或数据库结构。
只采集真实 Tick 和交易所闭合 K 线，历史 Tick 无法完整回溯的部分保留缺口，
不接通交易或策略风险资格。
详见 [期权全选](DATA_MANAGEMENT_API.md#期权全选与新合约自动纳入2026-11-07)。

2026-11-07 用户另行明确授权期权共享连接与批量托管。API 对期权只启动一个既有
Feed 采集入口及一个既有 Pipeline 历史入口，以数据库参数生成本地运行快照供进程动态读取；
不再按每个期权合约启动进程，原单合约公开 CLI 保留。connector 分组复用公开 WebSocket，
共享原生 TradeTick/Bar 解析与数值校验；批量管理不复制交易引擎或接通执行客户端。
合约仍独立持有访问/写入锁和文件检查点，源队列有界，历史获取/物化限并发，
停用或删除单个合约不终止其余合约。存储归属、DB 配置真源、API 鉴权和策略风险门禁保持。


2026-11-07 用户要求期权按标的交易对管理，并允许清理既有期权历史。
在既有 API → 配置表 / 文件监控 → 两个共享期权进程的职责内增加标的级配置与
汇总展示；到期日、方向及行权价仍在具体合约来源和 Catalog 中区分。
原单合约 HTTP / CLI 和 ALL 范围保留兼容，标的级删除复用既有指纹与逐合约锁。
旧历史清理属于本次显式部署操作，不在启动路径中自动删除，不改模块依赖或存储边界。
详见[标的级管理](DATA_MANAGEMENT_API.md#期权按标的交易对管理2026-11-07)。


2026-11-08 用户要求首页账户数据按分钟持久化、读取持久化数据。沿既有
ExchangeService 与 API 生命周期实现进程内分钟采集，最新展示快照原子写入原 API
本地状态目录 `portfolios/`，以逐账户文件锁防止多 worker 重复更新。普通 portfolio
请求只读匹配账户配置的文件，总览汇总这些结果；首次缺失返回空值，更新失败保留旧值，
两分钟后标记过期。显式刷新资产保留即时查询并先落盘再读取。该机制替代 2026-11-07
首页请求仅依赖内存缓存的方式，原生查询内存缓存仍服务采集及既有实时入口。
不增加业务表、迁移、独立服务或存储边界，不修改账本、资产曲线证据、手动交易及风控输入。
详见[首页分钟快照](BINANCE_NATIVE_API.md#首页分钟快照2026-11-08)。


2026-11-08 用户明确要求首页及操作记录加入期权，并指定期权采用初始持仓基准加
后续操作计算最新持仓，不加入分钟快照。沿既有 API → connector → 操作账本实现，
基准、行权证据、成功水位和派生持仓保存在原 `ledger_sync` JSON，订单、真实成交及
期权资金流水复用原账本。首页读取时合并已保存推算结果，初始边界有交易时拒绝接受
歧义基准，失败和超出保留期的缺口不自动重设基准。无新表、数据库迁移或独立服务。
期权操作不推算实时价格和浮盈亏、不并入 USD-M 账户权益，也不接通期权交易或更改风控输入。
原生永续查询、分钟展示快照、资产曲线与行情存储职责保持不变。
详见[期权操作与持仓推算](OPERATIONS_API.md#期权操作与持仓推算2026-11-08)。


2026-11-08 用户要求业务后端拥有正式/开发两类租户和空模板。模板只保存用途、名称和可选说明，
不含品种、区间、账户、资金或密钥。租户从模板创建后才写入订阅意图和账户：开发租户仅虚拟账户
（初始资金和报价币种在账户上），正式租户沿用原密钥库。已有行情策略和交易所账户迁移到默认正式租户，
不把当前环境做成模板。引擎仍是同一进程，SQLite 账本在原 `account_id` 旁增加 `tenant_id`。
归档导出业务记录和引擎切片、卸下该租户运行状态并标记归档；恢复重新导入引擎切片；删除单独永久
移除租户和归档文件。不新增租户进程或纸交易类型。策略包商店是同日随后单独加上的，见下文。

2026-11-08 用户要求把用户和租户归属落到现有 `users` 表上，不另建用户体系。
每个用户恰好一个正式租户，在创建用户时自动准备。升级时，名为「默认正式」的默认正式租户
归最早创建的用户；当时已有的其他用户各自得到一个正式租户。若还存在额外的正式租户，
其上的交易所账户行并入该最早用户的正式租户（移动，不复制密钥），额外租户改为开发租户。
没有用户的库则把这套归属留给之后第一个创建的用户。用户可以创建、归档、恢复和删除自己的
开发租户，不能再创建、归档或删除正式租户。复制和分享只复制行情订阅意图（品种与数据区间）。
虚拟账户和初始资金不复制，接收方在新的开发租户里自行设置。交易所密钥、secret 和 passphrase
不复制。分享给其他用户的副本只能是开发租户。管理员仍使用现有 Next.js 界面，在独立的用户管理
区域创建用户，并把当前会话切换成该用户的服务端视图；界面始终显示正在查看谁。切换期间，
租户接口按被查看的用户授权。空模板接口保留，新的开通路径不从模板创建。


## 策略商店

2026-11-08 用户要求业务后端拥有策略商店，并由引擎按租户加载已下发的包。
商店在 `api/strategies/`，不是新的进程。表为 `strategy_packages`、`strategy_package_versions`、
`strategy_grants`、`strategy_issuances`。版本保存入口、包哈希和 zip 归档。
`code_key` 来自归档里的 `name`，不是引擎允许清单。`strategy_python/` 已删除。

授权按租户类型分开。新建版本没有任何授权。开发租户要先有 `development` 授权才能下发；
正式租户必须另有 `production` 授权，开发授权不能代替它。下发调用引擎
`POST /tenants/{tenant_id}/strategy-loads`，与密钥下发一样由业务端发起，引擎拒绝用途不匹配、
未登记和已归档的加载。运行记录在引擎 SQLite `strategy_loads`，随租户导出、归档和删除。

当前唯一示例是 `packages/ema_crossover/`：Nautilus `ExponentialMovingAverage` 快慢线，
金叉公布满额多头数量，死叉公布零。策略不调用 `submit_order` 或 `close_all_positions`。
同一账户上的目标进入既有 `PortfolioCoordinator`；引擎先按各下发参数里最紧的 `risk_limit`
截断合计目标，协调器再用 `target_netting` 对 `Portfolio.net_position` 计算差额，
只提交带 `portfolio-net-delta` 标记的订单，由 Nautilus RiskEngine / ExecutionEngine 检查。
`POST /tenants/{tenant_id}/accounts/{account_id}/strategy-preview` 用模拟 venue 喂入 K 线，
用来证明这条路径。它不是实盘，也不读取交易所密钥。

开发租户在声明自己的行情订阅后，可以用虚拟账户的初始资金和报价币种，
对已下发的包跑一段历史：`POST /api/tenants/{tenant_id}/accounts/{account_id}/backtests`，
结果用 `GET .../backtests/latest` 读取。K 线来自现有 Catalog（`data/catalog`），
不另建行情库，也不下载。结束时间不包含在区间内，单次最多 366 天。
引擎先走已有 `POST /tenants/{id}/runnable` 的 `backtest` 门禁（归档租户拒绝），
再要求租户类型是开发。正式租户即使已加载正式授权，也不能走这条研究回测；
开发授权也不能加载到正式租户。回测不读取密钥、不发真实订单，也不新增任务表。

研究目录 `/api/management` 的回测动作不再启动已删除的 `strategy_python` 策略。
项目检查仍走原来的管理任务。

管理路由在 `/api/strategy-packages` 与 `/api/tenants/{tenant_id}/strategy-loads`，均要求管理员会话。
本轮没有新的租户页面，也不新增交易任务或纸交易租户。
