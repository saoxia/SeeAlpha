# Engine 独立仓库前的代码依赖边界

## 本轮完成的依赖切割

- 跨 HTTP 的 `EngineConflict`、`QueryError`、`AccountSnapshot` 提取到轻量
  `core/engine_contracts.py`，API 和 Engine 使用同一实现；旧 Engine 导入位置继续
  导出相同类型，避免改变异常捕获、序列化与现有测试行为。
- `api/engine_client.py` 已删除进程内 `EngineRuntime` 构造、调用及兜底，
  始终通过 `httpx` 调用 Engine 的 HTTP 契约；独立 Engine 未启动时不能回退为业务
  进程内执行。业务 API 不再拥有 Engine Runtime 生命周期。
  API 集成测试使用 Engine FastAPI 应用和 HTTPX 测试传输；第 14 项还在独立
  Engine Docker 容器重启后，以只包含 API/core 的隔离 Platform 进程完成 HTTP
  版本、租户路由及错误响应验证。此项仍不等同于真实交易环境验收。
- Engine Docker 构建只复制 `engine/`、`core/`、
  `nautilus_support.py`、策略示例 `packages/` 与锁定依赖，不再复制
  `api/`、`web/`、迁移或业务后台源码。

本轮**不**拆分仓库、不改变业务 API、不改变线上部署模式，也不迁移真实账户。

## 第 8–14 项合并后的当前边界（2026-11-09）

此前的“尚未切割”模块清单是第 8–14 项实施前的任务清单，不能继续当作现状。
现已验证：

- 生产 Platform 不直接导入 `engine.*` 或 `nautilus_trader`；业务端通过
  `api/engine_client.py` 以版本化 HTTP 请求调用 Engine，不可用时不得退回本地执行。
- 交易所只读数据与账户相关查询、操作流水、行情目录及管理任务经 Engine HTTP 提供；
  Platform 继续持有业务授权、订阅意图、核算和账本记录。
- Feed、历史补齐与数据任务的真实执行、状态和恢复归 Engine；策略包验证在 Engine
  的有界工作进程内进行。这是执行隔离，不是不可信第三方策略的完整安全沙箱。
- Engine Docker 镜像不包含 API/Web/业务迁移；独立容器和隔离 Platform 的 HTTP
  联调、版本错误和租户拒绝路径已在
  [PR #38](https://github.com/saoxia/SeeAlpha/pull/38) 关联 CI 中验证。

**后续仍需验收**：真实账户与下单、长期稳定性、生产状态卷备份恢复、
完整升级兼容矩阵。当前同宿主机多租户不等于跨机器调度；
这些缺口不应被描述为剩余 Python import 问题。

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

- 当前支持的 Engine HTTP 主契约版本为 `2`，使用 `X-SeeAlpha-Engine-Contract-Version: 2` 请求/响应头，`GET /health` 的 `contract_version` 字段用于运维核查。
- 生产 Platform 的所有 Engine HTTP 请求（同步、异步、数据控制、任务、导出、估值与流水流式读取）均携带版本头。所有成功响应必须带相同版本；没有版本头、版本不同或 Engine 返回 426 时立即失败，**不允许自动降级或回退到 Platform 本地执行**。
- Engine 拒绝明确声明为不兼容版本的请求，响应 `426`，并在响应头返回其版本。为避免破坏尚未改造的内部运维客户端，Engine 暂时允许不带版本头的请求，但它们仍受原有令牌和租户鉴权；业务 Platform 不使用此兼容路径。
- `2` 的请求/响应字段及既有 401/403/409/422/503 等语义不得在小版本中无通知地改变。增加不兼容字段或错误语义时，需发布新主契约版本并验证支持矩阵；旧 Platform 与新 Engine 混用必须明确验证或拒绝，而不是凭镜像标签推断兼容。
- CI 测试覆盖有效版本、请求版本不匹配、响应版本缺失或不匹配，以及每租户 Engine 的独立 token 路由。此版本策略是服务契约约束，**并不等于实盘行为验收**。

## 2026-11-09：人工交易命令归 Engine（HTTP 契约 v2）

- 删除可直接发送交易命令的 `POST /tenants/{tenant_id}/accounts/{account_id}/manual`，账户交易状态改用只读意图的 `POST .../trading/state`；原生快照查询 Node 保留，但不可从该接口执行下单或撤单。
- `POST .../orders` 仅接受经过共享请求模型校验的 `submit`/`cancel`。Engine 根据规范化订单参数计算幂等请求指纹，提交单的客户订单号由 UUID 派生，撤单指向已有客户订单号。请求预登记、账户级锁和未知结果不自动重发的行为保留。
- Engine 独占交易执行事实：`POST .../requests/{request_id}/reconcile` 从交易所状态核实并持久化已观察到的订单；`POST .../requests/{request_id}/ledger-sync` 只确认 Platform 已摄入订单记录，不再接受业务端 PUT 覆盖整个请求记录。
- Platform 继续负责用户授权、账户修订一致性、业务账本摄入和展示。v2 的 Engine 与 Platform 必须协调升级；旧 v1 HTTP 交易客户端不兼容。已有订单请求的 SQLite JSON 无需自动迁移，历史未知结果仍以实际交易所观察确认。
- 本阶段不启动实盘交易；模拟/隔离环境的单元与集成测试不构成真实账户、真实交易所、生产灾难恢复验收。
