# 项目操作记录 API

2026-10-27：操作记录属于 SeeAlpha，列表只读项目持久化账本。
Binance 是外部证据来源之一，不是页面查询时透传的响应。
当前接入 Binance USD-M 永续；没有实盘下单接口。

## 接口

| 方法 | 路径 | 权限与用途 |
| --- | --- | --- |
| GET | `/api/operations` | 登录用户；筛选并分页查询统一记录 |
| GET | `/api/operations/{id}` | 登录用户；查询投影及原始观察证据；合并前的旧 ID 仍可解析 |
| POST | `/api/operations/sync` | 管理员；通过 Nautilus 原生节点补充交易所证据 |
| GET | `/api/operations/export` | 登录用户；按相同筛选条件导出项目记录 CSV |

查询支持 `account_id`、`environment`、`strategy_id`、`source`、`event_type`、
`symbol`（品种标识包含匹配）、`start_ms`、`end_ms`（毫秒，包含边界）、`limit`（1–500）、`cursor`。
返回 `records`、`next_cursor`、`has_more`、`warnings`、`sync`。
游标包含时间和稳定记录 ID，作为不透明字符串原样传回，翻页时保留相同筛选条件。

记录保留 `account_id`、`environment`、`exchange`、`symbol`、`event_type`、`timestamp`、
`client_order_id`、`order_id`、`trade_id`、`strategy_id`、`source`、`confirmed`、
数量、价格、手续费及状态。金额使用十进制字符串。当前原生品种标识例如 `ETHUSDT-PERP`。

`source`：

- `project`：有本项目操作证据，可关联策略；没有交易所确认时 `confirmed=false`。
- `external`：交易所存在、项目尚无归属证据；不能自动认定为手工。
- `reconciliation`：项目运行时明确记录的对账修正，不是真实成交。

`event_type`：`place`、`amend`、`cancel`、`fill`、`reject`、`expire`、`correction`。
交易所导入的 `place` 表示存在该订单及其状态，不保证能够重建全部下单、改单过程。

同步请求 JSON 可包含 `account_id`、`start_ms`、`end_ms`。
未指定账户时遍历当前配置账户；默认最近 24 小时，起点须在最近 87 天内。
不支持的账户、网络错误、解析缺口会反映为账户同步失败，已有记录保留。
API 进程默认启动每 300 秒一次的同步循环，`OPERATION_SYNC_SECONDS=0` 可关闭。
当前实现保守地将交易所历史标为 `complete=false`；后台运行不代表历史完整。

## 记录与关联

`api/operations/repository.py` 提供后端 `Operations.ingest(Observation)`，不向浏览器开放
伪造策略来源的写入端点。项目事件可以先于交易所确认入库，拒绝和失败操作可保留。

- 按账户、环境、交易所、品种隔离身份。
- 真实成交通过 `trade_id` 去重，多笔部分成交分别保留。
- 使用 `client_order_id` 与交易所 `order_id` 关联。迟到的映射会合并重复投影，保留详情别名。
- 有项目发起操作的证据后，可将同订单已导入的外部成交关联到策略；单纯策略认领不作为发起证据。
- 同一订单出现冲突策略归属时拒绝写入，不自动选择归属。
- 每个观察事件内容不可改写；相同事件重试幂等，同一事件 ID 携带不同内容时拒绝。
- 投影可随新证据更新；交易所已确认的金额字段不被迟到的项目请求覆盖。
- 通用 ORM 路径在账户、环境、交易场所和交易品种范围内持有事务锁，完成合并、证据写入及来源更新。

## Nautilus 接入边界

本机 `nautilus-trader==2.0.0rc7` 的 Rust 执行对账支持原始报告，但 Python 通用消息
订阅不能接收这些原生类型（会报告 `non-PyMessage`）。本轮实际验证使用公开
`LiveNode.cache.orders()` 和订单执行事件读取，未调用私有 HTTP 客户端或 CCXT。

读取节点仅作查询，不安装下单策略，不修改杠杆和保证金。
原生对账创建的临时合成仓位订单会排除；仅保留具有 Binance 数字成交 ID 的真实成交。
状态报告推断的成交不视为真实成交。公开缓存投影不能替代完整的原始报告审计链，
交易所保留期、可发现品种、接口截断和 Python 暴露范围都可能造成历史缺口。

项目执行运行时可使用 `OperationService.record_project_event(event, order, account)`
持久化真实 Nautilus 项目事件，其中 `account` 必须是运行时绑定的业务账户。
`LedgerObserver.capture_event()` 提供事件缓冲，执行进程应在自己的订单事件回调中喂入，
并由工作线程取出 `drain()` 后执行 `Operations.ingest()`；不要在原生执行回调线程进行
网络或数据库 I/O。本轮没有启动实盘策略，也没有将回测事件写入实盘业务账本。
后续实盘执行节点必须显式接入该持久化入口，不能仅依靠短时查询节点恢复策略归属。

## 存储与前端

通用 ORM 将操作记录保存在 `ledger_operations`、`ledger_observations`、
`ledger_aliases`、`ledger_sync`。服务端通过 PostgreSQL 连接访问，应用接口只新增观察证据；
投影合并时可以更新证据关联的 ID。新库与历史库的权限配置各自核对。


本机检查的旧 `operation_records` 为 0 条，无需迁移历史操作。旧 SQLite 表仍保留，
后续不会由新列表读取。其他旧权益、快照、人工资金调整接口不属于本轮范围。

前端实时账户页和数据管理页的操作列表、同步和操作导出均使用新 API。
旧 `/api/exchange-accounts/*/trades` 及 `/api/data-management/operations` 不再由这些页面调用。
账户切换、列表轮询和同时间戳分页使用稳定记录 ID；来源在列表中明确显示。
其他数据集的导出继续使用旧 API。

验证包括 SQLite 并发去重、迟到归属、身份桥接、账户/环境隔离、鉴权、CSV 导出，
实际原生适配器连接本机模拟 Binance，前端路径/来源/轮询合并测试，以及
`tests/sql/operations.sql` 对云端实际 RPC 的回滚事务校验。云端 SQL 测试不留下演示记录。

本轮验收：Python 全量 1,355 项、前端 6 项测试通过，Ruff、ESLint 和生产构建通过。
真实前端代理查询、同步、导出与鉴权通过；当前配置账户为 0，尚未使用真实 Binance
私有凭据验收历史同步。
