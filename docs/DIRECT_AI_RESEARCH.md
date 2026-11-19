# SeeAlpha AI 研究 MCP 调用链（2026-11-20）

## 默认结构

```text
SeeAlpha Web → Platform（用户/租户/任务授权）→ Harness 常驻 Worker
                                                     │
                                         DeepSeek SDK MCP Client
                                                     │ 任务级 Platform Capability
                                                     ▼
             Platform /api/research/tasks/{task_id}/mcp（权限/预算/审计）
                                                     │ 每次签发 60 秒 Grant
                                                     ▼
                         Engine POST /research/mcp（开发租户）
                                      ├─ 数据集、快照元信息
                                      ├─ 已下发策略的开发模拟回测
                                      └─ 受限/可信 Python 候选回测（额外开关）
```

**不引入独立 Gateway、MCP Bridge 或每任务 Docker。** Engine 的 MCP 是原有受保护 Research API 的工具适配层，不是可从公网访问的通用交易服务。Harness 只获得当前任务的短期 Platform Capability；Engine 研究 Grant、Engine 管理令牌、签名密钥及 Engine 地址均由 Platform 控制面持有，不进入 Agent Prompt、MCP profile、会话历史或成果。

## 实现位置

- Harness `seealpha_harness/worker.py` 和 `mcp_profile.py`：已绑定研究权限的任务自动加载 `@deepseek-ai/dsh-mcp-client`，通过环境动态解析 Platform 任务凭据。普通任务不加载研究 MCP。
- Platform `api/research/task_router.py`：任务专属的无状态 MCP Streamable HTTP JSON 端点，逐次核验有效 Task/Turn、归属、撤销状态、调用次数；无任意 URL、交易和管理工具。
- Platform `api/research/task_access.py` + `mcp_engine.py`：按账户、数据集和候选版本核验业务授权，签发操作级短期 Grant；调用 Engine 原生 `/research/mcp`，不允许无声回退到 HTTP v1 工具。
- Engine `engine/research_mcp.py`：原生 MCP 工具严格复用开发 Research API 的权限和任务幂等性。Engine 没有暴露实盘交易或管理工具。
- 大文件快照仍需经 Platform 固定的 `snapshot_fetch` 命令访问 Engine 受保护的 `/research/v1/snapshots/{id}/content`，Harness 验证长度/SHA-256 后写入专属工作目录；MCP 不携带 Parquet base64。AI 策略源码 ZIP 仍先通过 Platform 候选登记 API 静态校验，MCP 只按不可变候选 ID 提交。

## 部署和失败语义

`RESEARCH_PROXY_ENABLED=true` 仅允许 Platform 为**新创建的开发租户 Engine Docker** 写入 `ENGINE_RESEARCH_SIGNING_KEY` 和 `ENGINE_RESEARCH_MCP_ENABLED=true`；`RESEARCH_PYTHON_ENABLED=true` 另控制可信 Python 候选通道。研究模式默认关闭，现有 Docker 的 env/状态不自动重写。运行中的旧 Engine 如未显式更新到包含原生 MCP 的版本，研究请求会失败，不退回旧 REST。

Harness 研究 Task 的 Platform Capability 仍然是 15 分钟、每轮最多 80 次研究调用；MCP 握手和工具发现也占用预算。Platform 每次重新检查任务运行状态，Engine 每次验签 60 秒 Grant。取消、失效、未知响应、网络失败与工具错误不得视为提交成功；重复提交应使用已有幂等键并先检查任务事实。

当前单个 Harness 容器中 Agent Shell 与控制进程同 UID，能读取自己的任务级能力。**此结构不是对不可信多租户 Python 的强沙箱。** 生产交易、Engine 高权限密钥、正式策略审批和生产发布仍属独立信任边界。Engine 中完整 Python 策略也只对可信开发租户额外显式开启。

## 验证

包含 Platform MCP 客户端协议/拒绝/二进制校验测试，任务 MCP 认证/撤销测试，以及 Harness SDK 配置/身份失败测试。它们为合成测试；需要在隔离开发主机启用三个服务、受限数据与测试模型，验证真实 SDK 协议协商、联网、回测队列与取消/恢复后，才能称真实端到端通过。

相关文档：[Platform 研究配置](AI_RESEARCH_COMMANDS.md)、[Engine MCP](https://github.com/saoxia/seealpha-engine/blob/main/docs/MCP_RESEARCH.md)、[Harness 配置](https://github.com/saoxia/seealpha-harness/blob/main/docs/PLATFORM_RESEARCH_COMMANDS.md)。
