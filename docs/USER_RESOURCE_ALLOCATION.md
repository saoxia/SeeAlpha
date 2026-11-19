# 用户资源授权和配额

管理员在「用户管理 → 配置资源」中为普通用户分别分配 AI Harness、开发 Docker 和正式 Docker。授权开关与容器运行状态独立；修改权限不会自动停止已有任务或容器，正式 Docker 仍必须选择节点、确认部署。

资源参数默认继承平台环境变量（Harness: HARNESS_CPUS / HARNESS_MEMORY；Engine: ENGINE_DEVELOPMENT_* / ENGINE_PRODUCTION_*），可在用户配置中覆盖 CPU、内存。单个用户的每类资源可独立启停。部署时读取数据库授权和配额；对于已经创建的容器，禁止在此页面直接更改配额或部署节点，需走备份、订单检查和受控升级程序。现有开发租户存在时将拒绝直接更改其资源参数；已有 Harness 控制凭据时同样拒绝。已有正式 Docker 绑定后不允许直接撤销授权或修改配额，以防停止风险检查前的交易进程。

后端持久化：user_resource_settings（完整的三类配置 JSON，按用户 ID 唯一）。没有配置行时三类资源使用资格默认均为 true，但**开发 Docker 的 realtime_enabled 默认 false**。是否实际可用仍受系统 HARNESS_ENABLED、HARNESS_DEPLOYMENT 和 ENGINE_DEPLOYMENT 控制。首次保存后将建立配置行。

开发实时行情是开发 Docker 的独立产品能力开关，管理员可在「用户管理 → 配置资源 → 开发 Docker」直接调整，且不受 CPU / 内存和节点调整锁定的影响。未开通时，普通 Platform 流程新建/保存开发行情订阅以及复制/分享至开发租户的配置均使用 `realtime_enabled=false`；历史数据、回测能力不变。开通后 Platform 接受开发租户的实时订阅意图，但不会自动启动实时采集。该开关不影响正式租户及全局数据管理所绑定的系统默认行情租户。它不是安全隔离措施，不修改独立 Engine，也不限制绕过 Platform 的访问；更改开关不会自动停止已在运行的采集任务。

管理员接口：
- GET /api/users：包含每位普通用户的 resources 配置
- GET /api/users/resource-capabilities：获取系统默认配额和在线节点
- GET /api/tenants/capabilities：普通用户查询自己的开发实时行情可用性
- PUT /api/users/{user_id}/resources：一次性保存三类资源的授权和覆盖值
- POST /api/users/{user_id}/engine：保留已有正式 Docker 显式部署流程（未授权时拒绝）

对 disabled 的 Harness 用户拒绝调用 /api/harness/*。开发 Docker 撤销授权后禁止再创建开发租户、提交新回测或加载策略；已运行任务需要运维流程关闭。正式 Docker 禁用针对尚未部署的租户，不允许绕过正式部署授权；已绑定节点禁止直接关闭以保护运行资金。部署节点变更不自动搬迁状态卷或会话历史。

远端节点需来自配置的在线 Node Agent；Harness 远端节点仅在 per_user 模式允许，开发 Engine 远端节点仅在 distributed 模式允许。正式节点继续使用现有不可直接变更的持久化部署绑定；页面不会提供第二套绑定系统。

注意：Harness 的真实模型凭据/安全代理依然是独立的服务就绪条件。资源授权不会自动注入模型 Key，不会给予策略实盘授权，也不代表 Agent 已通过生产级验收。
