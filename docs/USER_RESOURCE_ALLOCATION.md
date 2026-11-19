# 用户资源授权和配额

管理员在「用户管理 → 配置资源」中为普通用户分别分配 AI Harness、开发 Docker 和正式 Docker。授权开关与容器运行状态独立；修改权限不会自动停止已有任务或容器，正式 Docker 仍必须选择节点、确认部署。

资源参数默认继承平台环境变量（Harness: HARNESS_CPUS / HARNESS_MEMORY；Engine: ENGINE_DEVELOPMENT_* / ENGINE_PRODUCTION_*），可在用户配置中覆盖 CPU、内存。单个用户的每类资源可独立启停。部署时读取数据库授权和配额；对于已经创建的容器，禁止在此页面直接更改配额或部署节点，需走备份、订单检查和受控升级程序。现有开发租户存在时将拒绝直接更改其资源参数；已有 Harness 控制凭据时同样拒绝。已有正式 Docker 绑定后不允许直接撤销授权或修改配额，以防停止风险检查前的交易进程。

后端持久化：user_resource_settings（完整的三类配置 JSON，按用户 ID 唯一）。旧用户没有配置行时沿用原行为：三项用户级授权均为 true，是否实际可用仍受系统 HARNESS_ENABLED、HARNESS_DEPLOYMENT 和 ENGINE_DEPLOYMENT 控制。首次保存后将建立配置行。

管理员接口：
- GET /api/users：包含每位普通用户的 resources 配置
- GET /api/users/resource-capabilities：获取系统默认配额和在线节点
- PUT /api/users/{user_id}/resources：一次性保存三类资源的授权和覆盖值
- POST /api/users/{user_id}/engine：保留已有正式 Docker 显式部署流程（未授权时拒绝）

对 disabled 的 Harness 用户拒绝调用 /api/harness/*。开发 Docker 撤销授权后禁止再创建开发租户、提交新回测或加载策略；已运行任务需要运维流程关闭。正式 Docker 禁用针对尚未部署的租户，不允许绕过正式部署授权；已绑定节点禁止直接关闭以保护运行资金。部署节点变更不自动搬迁状态卷或会话历史。

远端节点需来自配置的在线 Node Agent；Harness 远端节点仅在 per_user 模式允许，开发 Engine 远端节点仅在 distributed 模式允许。正式节点继续使用现有不可直接变更的持久化部署绑定；页面不会提供第二套绑定系统。

注意：Harness 的真实模型凭据/安全代理依然是独立的服务就绪条件。资源授权不会自动注入模型 Key，不会给予策略实盘授权，也不代表 Agent 已通过生产级验收。
