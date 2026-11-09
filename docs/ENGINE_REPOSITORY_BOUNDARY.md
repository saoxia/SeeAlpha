# Engine 独立仓库前的代码依赖边界

## 本轮完成的依赖切割

- 跨 HTTP 的 `EngineConflict`、`QueryError`、`AccountSnapshot` 提取到轻量
  `core/engine_contracts.py`，API 和 Engine 使用同一实现；旧 Engine 导入位置继续
  导出相同类型，避免改变异常捕获、序列化与现有测试行为。
- `api/engine_client.py` 已删除进程内 `EngineRuntime` 构造、调用及兜底，
  始终通过 `httpx` 调用 Engine 的 HTTP 契约；独立 Engine 未启动时不能回退为业务
  进程内执行。业务 API 不再拥有 Engine Runtime 生命周期。
  API 集成测试使用 Engine FastAPI 应用和 HTTPX 测试传输，验证相同的 HTTP 路由，
  但此类测试不等同于两个真实独立进程的全链路验收。
- Engine Docker 构建只复制 `engine/`、`core/`、
  `nautilus_support.py`、策略示例 `packages/` 与锁定依赖，不再复制
  `api/`、`web/`、迁移或业务后台源码。

本轮**不**拆分仓库、不改变业务 API、不改变线上部署模式，也不迁移真实账户。

## 拆仓前仍需完成的解耦（不能宣称已独立）

当前业务 API 的以下模块仍直接依赖 Engine 实现代码：

| API 模块 | 剩余依赖及建议处理方式 |
| --- | --- |
| `api/exchanges/service.py` | Binance 账户资产、品种及现货/合约行情查询；在 Engine 增加受限只读 HTTP 能力 |
| `api/exchanges/trading.py` | 手动交易市场行情；改为 Engine 查询接口 |
| `api/equity/service.py` | 资产估值外部取价及对账查询；改为受授权的 Engine HTTP 查询，API 保留核算逻辑 |
| `api/operations/service.py` 与 `options.py` | 交易所操作与期权历史；增加受控分页查询接口 |
| `api/market_data/router.py`、`service.py`、`subscriptions.py` | 一次性任务、永续/期权实时采集进程，以及 Catalog 清单、最新数据时间、覆盖率、历史预览、图表、删除快照已由 Engine 独占；Platform 保留业务 ORM 订阅意图、期权目录发现与 HTTP 同步；历史起点解析及实时状态读取统一通过 Engine 配置预览与 HTTP 状态接口，Platform 不再读取 Engine 数据卷；其余交易和策略模块仍待切割 |
| `api/market_data/monitor.py` | 平台已不再采样/写入服务监控，仅从 Engine HTTP 读取；已删除 `observe` 本地文件状态读取，仅保留 Engine HTTP 监控代理 |
| `api/strategies/service.py` | 动态策略包校验与参数准备依赖 `engine.strategies.package`；需将校验放在受隔离的研究运行时或通过 Engine 授权接口调用 |

**禁止仅将 import 路径改名、复制 Connector/回测实现到 API 或把所有
Engine 实现移动到 `core` 来掩盖耦合。** 需要真正的远程协议隔离和现有
调用/异常/幂等行为回归验证。

本轮进一步删除了 `api/market_data/service.py` 内的 API 本地任务进程、任务结果读取与恢复逻辑。
`EngineDataService` 独占任务操作记录、交易对排他、超时取消和中断恢复；Platform 只通过 Engine HTTP
获取任务状态，并在 Engine 确认删除/裁剪成功后使用 `.local/state/api/market_settlements/` 的幂等结算标记
更新业务配置。旧 API 任务目录不会被 Engine 自动接管。实时订阅工作进程也已归属 Engine；本地 Catalog 读取仍需后续独立清理。

本轮行情监控状态（`/api/data/services`）与 Feed 实时状态（`/api/data/feed/status`）已改为只读 Engine HTTP，不在 Platform 主机目录查找监控文件。Engine 内继续负责过期、字段与租户验证。只读状态以 Engine 为真源；部分用于旧测试的本地数据文件投影仍保留，尚待移除。

本次数据查询清理：Platform `api/market_data/router.py` 不再导入 NautilusTrader、Pipeline
Catalog 或本地行情工作集，也不使用宿主机锁判断数据可读。库存、最近事件时间、覆盖率、
准备预览、分页、实时图表统一调用现有 Engine HTTP 数据接口。Platform
`DataService` 的存储检查、归档计划、删除计划和裁剪方案也不再本地计算。
Engine 缺失时显式 503，不创建本地替代目录。读取租户数据卷、共享访问锁以及
历史快照校验始终由 Engine 执行。尚待清理的是 `Subscriptions` 中历史本地
状态投影，以及订阅起始日期解析中的某些直连数据实现。

本次订阅工作进程清理：Platform 的 `Subscriptions` 不再持有 `processes/auxiliary`，
也不直接执行 `engine.feed.managed`、`engine.pipeline.pair_data`，不管理期权共享进程，
不会在业务 API 停止时关闭 Engine 工作进程。业务库配置变更后调用 Engine `sync`；
业务 API 周期性恢复只重发 ORM 订阅意图。行情访问许可、进程恢复、期权批处理 manifest
均由 Engine 负责。平台使用 `.local/run/api/market_policy_writer.lock` 保护业务配置写入；
Engine 的采集进程继续使用独立的租户采集锁。即使两者运行在同一宿主机，也不共享锁名。

建议下轮优先隔离交易所只读查询，再迁移操作记录，最后迁移数据管理链路；
这些功能改造与未来独立仓库发布应分别验收。

## 完全独立的验收条件

1. API 生产环境及所有业务 API 模块导入无需 Engine 源码及 NautilusTrader runtime。
2. Engine Docker 镜像只包含 Engine 及共享契约所需的最小依赖。
3. 不启动 Engine 时 API 可返回明确的 503，生产下单和幂等不回落本地。
4. 回测、交易、期权、资金费率、市场数据管理、历史数据与策略包
   在两个独立进程中完成全链路测试。
5. 版本化的 Engine 镜像与 HTTP 契约兼容性验证通过，再拆为独立仓库。

## 第八批：行情业务模块完全不接触 Engine 文件与源码

- `Subscriptions.resolve` 中不再访问 Binance REST、Nautilus Catalog、SQLite journal 和 Pipeline。
  规范化日期和删除数据影响检测由 Engine `configuration-plan` 完成。
- `Subscriptions.items/_public` 仅从 Engine `subscriptions` 接口获取当前运行状态。
- `ServiceMonitor` 仅通过 Engine HTTP 读取服务健康。无本地状态 JSON 兼容投影。
- 保存和删除订阅仍由 Platform 授权，Engine `sync` 负责验证删除访问锁。

## 第十三批：Engine HTTP 契约版本策略

- 当前支持的 Engine HTTP 主契约版本为 `1`，使用 `X-SeeAlpha-Engine-Contract-Version: 1` 请求/响应头，`GET /health` 的 `contract_version` 字段用于运维核查。
- 生产 Platform 的所有 Engine HTTP 请求（同步、异步、数据控制、任务、导出、估值与流水流式读取）均携带版本头。所有成功响应必须带相同版本；没有版本头、版本不同或 Engine 返回 426 时立即失败，**不允许自动降级或回退到 Platform 本地执行**。
- Engine 拒绝明确声明为不兼容版本的请求，响应 `426`，并在响应头返回其版本。为避免破坏尚未改造的内部运维客户端，Engine 暂时允许不带版本头的请求，但它们仍受原有令牌和租户鉴权；业务 Platform 不使用此兼容路径。
- `1` 的请求/响应字段及既有 401/403/409/422/503 等语义不得在小版本中无通知地改变。增加不兼容字段或错误语义时，需发布新主契约版本并验证支持矩阵；旧 Platform 与新 Engine 混用必须明确验证或拒绝，而不是凭镜像标签推断兼容。
- CI 测试覆盖有效版本、请求版本不匹配、响应版本缺失或不匹配，以及每租户 Engine 的独立 token 路由。此版本策略是服务契约约束，**并不等于实盘行为验收**。
