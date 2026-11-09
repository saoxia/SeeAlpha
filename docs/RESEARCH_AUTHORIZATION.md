# Platform → Engine Research 任务授权（内部服务）

> 当前仅实现 Platform 内部签发器与可信 Gateway Client；尚未接入 Web/Harness 的正式任务控制面，也未开通 Grant 签发 HTTP 路由。**不进行实际机器验证**。

## 已实现边界

- `api/research/grants.py`：对开发租户检查真实归属、active 状态、期限、数据集/策略范围、操作白名单，并依赖权威资源策略；生成与 Engine Research API v1 匹配的 HMAC-SHA256 签名。默认最大 TTL 为 900 秒。
- `api/research/gateway_client.py`：服务端固定 Gateway Origin，调用 `/internal/v1/leases` 登记已签发的 Engine grant，得到任务专属的 Bridge 令牌，并支持 `DELETE /internal/v1/tasks/{task_id}` 撤销网关本地登记。
- 只返回公开的任务收据，**不得把原始 Engine Grant / Bridge Token 发送到浏览器、日志、Agent 环境变量、任务 Artifacts 或 Session**。
- 当前研究授权仍仅用于开发数据、快照和模拟回测，绝无下单、撤单、生产策略启停或交易账户管理权限。

## 待接入的可信依赖（强制）

`ResearchGrantIssuer` 是内部服务，依赖四个真实组件，缺任何一个就不能启用完整授权流程：

1. `TenantRepository`：真实租户归属与开发用途；签发者必须是该租户所有者。当前仅管理员有策略发布能力，未来可将更细分的权限策略接入本服务，而不是信任前端。
2. `signing_key_for_tenant`：可信密钥提供者，读取**每个开发租户**与独立 Engine 运行实例一致的 `ENGINE_RESEARCH_SIGNING_KEY`。不得复用 Engine 管理令牌、用户密码或主站 Session Key。**现有 Tenant Docker Manager 尚未配置此密钥的安全分发**，因此不提供默认密钥。
3. `authorize_resources`：针对本租户实际 Catalog/下发开发策略的权威检查。当前不能由请求体自报已授权数据集，也不能仅靠字符串格式判断所属租户；此回调必须由正式服务实现并测试。
4. `record_audit`：持久审计插入，必须在返回 grant 前成功。现有实现只定义回调，未改动数据库模型和表；不可用就拒绝签发。

控制面还需实现真正的任务创建、租户会话审批、Gateway 登记、Agent/Bridge Sidecar 启动与失败回滚。因这些关键依赖尚未接入，**本次没有向 `api/main.py` 注册任何公开 Grant 签发接口**，避免把内部凭据误暴露给 Web。

## 请求与安全语义

- Engine Grant：`version=1`、`audience=seealpha-engine-research`、`tenant_id`、`task_id`、`ops`、`datasets`、`strategies`、`not_before`、`expires_at`。字段与独立 `seealpha-engine/engine/research_auth.py` 保持一致。
- 限定可用操作：`datasets:read`、`snapshots:create`、`snapshots:read`、`backtests:submit`、`backtests:read`、`backtests:cancel`；有效期 60–900 秒。Engine 每次请求仍需独立完成签名与范围验证。
- Gateway Client 使用静态可信服务控制令牌，不接受用户指定的 URL、任意 HTTP 方法或透传原始错误内容。`bridge_token` 只从可信服务端在登记时领取一次，不会出现在公共元数据里。
- Engine 的 Grant 过期不等于 Gateway 预算自动清理；失败/取消时应由可信任务编排器立即撤销网关 Lease。Engine 尚无已签发 Grant 的实时吊销列表，撤销后到自然过期之间存在残余窗口。

## 自动化验证

`tests/api/research_grant_test.py` 使用测试租户数据库，验证所有者/开发目的、资源白名单、短期时间范围、HMAC 格式、审计失败及密钥错误拒绝；`tests/api/research_gateway_test.py` 使用 `httpx.MockTransport` 验证版本、错误屏蔽、原始令牌仅经可信内部通道转交。没有启动 Engine、DeepSeek、Docker 或真实交易服务。

后续验收仍由用户决定时间。