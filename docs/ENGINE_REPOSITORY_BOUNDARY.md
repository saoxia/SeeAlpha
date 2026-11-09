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
| `api/market_data/router.py`、`service.py`、`subscriptions.py` | 数据库存、覆盖、准备/删除计划、ticks/bars 分页、K 线图表、最新 1s、任务及订阅健康状态均由 Engine HTTP 读取；Platform 只保留 ORM 配置意图、授权、期权目录发现、确认校验和任务成功后的业务结算 |
| `api/market_data/monitor.py` | 仅从 Engine HTTP 获取服务健康状态；旧文件观察与过期解析实现已删除 |
| `api/strategies/service.py` | 动态策略包校验与参数准备依赖 `engine.strategies.package`；需将校验放在受隔离的研究运行时或通过 Engine 授权接口调用 |

**禁止仅将 import 路径改名、复制 Connector/回测实现到 API 或把所有
Engine 实现移动到 `core` 来掩盖耦合。** 需要真正的远程协议隔离和现有
调用/异常/幂等行为回归验证。

本轮进一步删除了 `api/market_data/service.py` 内的 API 本地任务进程、任务结果读取与恢复逻辑。
`EngineDataService` 独占任务操作记录、交易对排他、超时取消和中断恢复；Platform 只通过 Engine HTTP
获取任务状态，并在 Engine 确认删除/裁剪成功后使用 `.local/state/api/market_settlements/` 的幂等结算标记
更新业务配置。旧 API 任务目录不会被 Engine 自动接管。实时订阅与 Catalog 读取亦已归属 Engine。

本轮行情监控状态（`/api/data/services`）与 Feed 实时状态（`/api/data/feed/status`）已改为只读 Engine HTTP，不在 Platform 主机目录查找监控文件。Engine 内继续负责过期、字段与租户验证。只读状态以 Engine 为真源；旧 API 本地文件解析及投影分支现已移除。

本次订阅工作进程清理：Platform 的 `Subscriptions` 不再持有 `processes/auxiliary`，
也不直接执行 `engine.feed.managed`、`engine.pipeline.pair_data`，不管理期权共享进程，
不会在业务 API 停止时关闭 Engine 工作进程。业务库配置变更后调用 Engine `sync`；
业务 API 周期性恢复只重发 ORM 订阅意图。行情访问许可、进程恢复、期权批处理 manifest
均由 Engine 负责。平台使用 `.local/run/api/market_policy_writer.lock` 保护业务配置写入；
Engine 的采集进程继续使用独立的租户采集锁。即使两者运行在同一宿主机，也不共享锁名。

第七批：`api/market_data` 已删除本地 Catalog/journal 数据读取、历史数据预览、
库存/时间覆盖查询、数据目录读写锁、服务状态 JSON 解析以及本地历史起点计算。
Engine 现有 `datasets/latest/coverage/prepare-plan/page/chart` 与
`control/delete-plan/pair-delete-plan/configuration-plan/subscriptions/services`
HTTP 路由负责数据真实性、时间边界、过期及排他验证。API 继续按用户鉴权并限制
已订阅品种；无 Engine 时明确拒绝，不能退回宿主机数据目录。
注意历史期权目录发现、外部交易所读取和策略校验仍有其他跨模块依赖，不等于
Platform 已可以不带任何 Engine 源码独立部署。

建议下轮优先隔离交易所只读查询，再迁移操作记录，最后迁移数据管理链路；
这些功能改造与未来独立仓库发布应分别验收。

## 完全独立的验收条件

1. API 生产环境及所有业务 API 模块导入无需 Engine 源码及 NautilusTrader runtime。
2. Engine Docker 镜像只包含 Engine 及共享契约所需的最小依赖。
3. 不启动 Engine 时 API 可返回明确的 503，生产下单和幂等不回落本地。
4. 回测、交易、期权、资金费率、市场数据管理、历史数据与策略包
   在两个独立进程中完成全链路测试。
5. 版本化的 Engine 镜像与 HTTP 契约兼容性验证通过，再拆为独立仓库。
