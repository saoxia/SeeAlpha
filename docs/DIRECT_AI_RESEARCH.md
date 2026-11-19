# AI 策略研究的简化调用链（2026-11-19 选定）

## 选定的默认架构

```text
SeeAlpha Web (登录/任务/结果)
       │
       ▼
Platform API (权限/审计/任务控制 + 轻量 Research 代理)
       ├──────────► Harness (常驻每用户 Agent, 自由研究与生成草稿)
       │                  │
       │         调用 Platform 暴露的受限研究命令
       │                  ▼
       └──────────► Engine /research/v1/* (开发租户)
                            ├── Catalog / Parquet 快照
                            └── NautilusTrader 模拟回测
```

不将独立 Gateway、MCP Bridge、每任务 Docker 或任意 Engine Shell 作为默认依赖。
模型要执行的是有限业务命令，不是在具有生产交易权限的 Engine 主机上运行任意 Shell。
Engine 管理 Token、交易密钥与开发研究令牌不能交给 Harness/Agent。
未来如需对外部 Agent 兼容 MCP，可以另加非必需 Adapter。

## 本次真实代码范围

- `api/research/direct_engine.py`：Platform **内部专用**的固定地址/固定操作研究 HTTP 客户端，使用已有经授权的 `ResearchGrant` 调用 Engine Research API v1。
- 支持查询数据集、创建/查询/有界校验快照、提交开发模拟回测、查询/取消回测。
- 禁止重定向、任意 URL/路径、通用 Engine 管理 API 和任意策略代码执行；限制 JSON/快照大小，验证合同版本与 SHA-256，错误不透传 Engine 原文。
- `platform/tests/research_direct_test.py`：用 `httpx.MockTransport` 模拟真实合同，验证拒绝越权响应、错误脱敏、数据完整性和访问路径白名单。
- 本次 **未注册 HTTP 公共路由、未将令牌注入 Worker、未连接真实 Engine、未改变资金或生产开关**。客户端的存在不等于 AI 已具备工具调用能力。

旧 `api/research/gateway_client.py` 与 Harness 的 Gateway/MCP 代码是**未启用的实验原型**，不是此架构的前置依赖。本次先保留已有测试与参考实现，不在未完成调用方审查时删除。

## 后续最小接线顺序（尚未实现）

1. Platform 可信任务控制器创建研究任务，验证真实用户/开发租户归属、资源配额和已授权策略/Data Catalog；记录持久审计并使用开发租户专属 `ENGINE_RESEARCH_SIGNING_KEY` 签发短时 Grant。必须使用真实密钥分发与资源授权，不得信任 Agent、浏览器请求体中的声明。
2. Platform 将有限的研究命令绑定到 Harness 的**当前任务身份**；调用入口必须严格检查任务归属、数据集/策略范围、可用次数、资源预算、超时与撤销。Agent 只持有最多限于该任务的研究调用能力；不得持有 Engine Grant、通用 API/模型密钥或生产权限。当前常驻 Harness 的自由 Shell 与控制进程同 UID，不是强沙箱；客户不可信代码上线前必须检验额外隔离和出网限制。
3. 通过 Platform 内部客户端调用 Engine 的开发研究 API。大文件通过有界、校验哈希、只读的授权传输交付任务研究目录，不能开放任意下载 URL 或把数据注入浏览器。当前原型内存转交上限为 16 MiB，非生产级流式传输。
4. 在隔离开发环境安全接收 AI 策略包、校验依赖并映射到可回测的版本。目前 Engine **仍只回测预先下发的开发账户单品种策略**，不接受任意 AI 生成 Python 路径。新策略动态回测和多产品回测需要后续 Engine 变更。
5. 记录每轮假设/代码版本/数据指纹/回测/样本外结果；AI 可继续迭代，最终创建**候选**策略包；正式发布/实盘需要另行人工审批与独立权限。

## 保持的安全限制

- Engine Research API 在开发租户默认关闭；生产 API 不因研究代理而获得任何新入口。
- Platform 与 Engine 各自执行权限检查；反向代理不可跳过 Engine 的 HMAC 验签、数据和策略作用域、任务幂等。
- 研究任务停止、取消、失效时不得盲目重放；未知结果按失败待核实处理。
- 合成 Mock 测试不代替真实 Docker、模型、跨仓库和不可信执行环境验收。

相关技术合同：[Engine Research API](https://github.com/saoxia/seealpha-engine/blob/main/docs/RESEARCH_API.md)、
[现有内部 Grant](RESEARCH_AUTHORIZATION.md)、
[Harness 当前集成](HARNESS_INTEGRATION.md)。
