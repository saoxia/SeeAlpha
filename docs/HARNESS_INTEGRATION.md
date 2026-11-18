# 在 SeeAlpha Platform 接入独立 Harness（本地开发阶段）

> 范围：**独立 `saoxia/seealpha-harness` 常驻服务** → SeeAlpha Platform API → RESEARCH / AI Harness。不会嵌入独立 Harness 运行时代码、安装 DeepSeek SDK 到 Platform，也不会替换独立 Engine 或创建每任务 Docker。

## 架构和当前资格

```text
登录用户 → SeeAlpha Web /RESEARCH → 同源 /api/harness/*
          → Platform Session 与管理员权限检查
          → 固定来源 Harness HTTP v1（Bearer 只在服务器）
          → 唯一常驻 Harness Docker → 顺序队列 / 每任务独立 Worker 进程
```

**默认关闭，现阶段只支持未代入其他用户的管理员本地开发。** 这不是生产多租户 Agent 沙箱：Harness 任务内的 DeepSeek SDK 自由 Shell 与控制器仍属于单容器/同 UID；不能保证租户工作区间强隔离，模型测试凭据可能被 Agent 读取。禁止把生产交易密钥、共享租户私有行情或生产 Engine 凭据交给 Agent。Platform/Harness/Engine 的真正可信 Research Gateway、任务级 Grant、SDK MCP 工具隔离与 Engine 端到端回测授权仍未接通。

本阶段可以在 RESEARCH 页提交任意本地 AI 研究提示、查询任务状态、取消、在已完成 Session 上继续、阅读最终回答、列出并下载已登记 Artifact。**不声称已经能通过 Agent 直接运行 Engine 开发回测。**

## 启动

Harness 沿用其仓库的**一个常驻 Docker**，只在部署/升级时启动一次：

```bash
git clone https://github.com/saoxia/seealpha-harness.git
cd seealpha-harness
cp .env.example .env
# 使用全新的强随机 HARNESS_API_TOKEN 与有限额的 DEEPSEEK_API_KEY；
# 设置详细要求参考 Harness README 和 docs/SECURITY.md
docker compose up -d --build
curl http://127.0.0.1:8777/health
```

Harness 默认只监听宿主 `127.0.0.1:8777`，不要将其 API 端口暴露到公网、共享 LAN 或不可信 Agent 网络。SeeAlpha Platform 和 Harness 在同一宿主运行时，在**Platform API 进程**设置：

```bash
export HARNESS_ENABLED=true
export HARNESS_URL=http://127.0.0.1:8777
export HARNESS_API_TOKEN='<与 Harness .env 中完全一致的高强度密钥>'
```

`HARNESS_URL` 是**固定服务根地址**，不支持在请求参数指定 URL。若 Platform API 自身运行在另一容器或主机内，`127.0.0.1` 将指向该进程的网络命名空间；此时须配置可信内部可达地址，保持 Harness 管理 API 仅对可信 Platform 开放。两端之间应有真实网络边界，不能将 Harness 的控制 Token 注入不可信 Agent 工作区。

未设置 `HARNESS_ENABLED=true` 时，SeeAlpha 原来的登录、行情、回测、交易及管理 API 不依赖 Harness，也不受其断连影响。

## Web 操作

打开 SeeAlpha → RESEARCH → AI Harness，状态应为「Harness 已连接」。输入研究目标后点击「开始研究」。请求含 `queue_if_busy: true`：常驻 Harness 运行中时任务进入其有界 FIFO 队列，不会启动第二个 Docker。任务状态每两秒通过 Platform API 查询，完成后展示最终回答及已登记成果清单。点击成果名称经 Platform 强制附件下载；主动创建新会话不会删除 Harness 服务端记录。当前浏览器只记住最近一项任务 ID，不是跨用户/租户历史管理功能。

如果任务失败、中断或取消，重新提交将创建新任务；只有完成的 Session 可以续接。断开网络、提交超时等未知结果不会被自动重发，以避免重复运行。Harness 容器重启后，任务恢复与排队门禁由 Harness 自身 `TaskManager` 管理，Platform 不自动向 `/v1/queue/resume` 发控制命令。

## 安全与接口

- FastAPI 路由：`GET /api/harness/status`；`POST /api/harness/tasks`；`GET /api/harness/tasks/{id}`；`POST /api/harness/tasks/{id}/continue`；`POST /api/harness/tasks/{id}/cancel`；`GET /api/harness/tasks/{id}/artifacts`；`GET /api/harness/tasks/{id}/artifacts/{artifact_id}`。
- 所有路由经真实 SeeAlpha Session 身份校验，拒绝未登录、非管理员与管理员代入其他用户的会话；既有同源写操作来源检查不变。
- 只有 Platform 服务端传递 `Authorization: Bearer` 和版本 `X-SeeAlpha-Harness-Contract-Version: 1`。禁止任意 URL、通用 HTTP 转发、跳转及透传上游鉴权错误。API Token 不会进入浏览器、成果、任务 Payload 或日志。
- Artifact 仅以 `application/octet-stream`、`Content-Disposition: attachment` 和 `nosniff` 交付，限制 16 MiB；不将未审查 HTML/JS 作为主站内容执行。
- 研究过程不会向 Engine 下单或修改风控；不存在生产 Grant、真实回测结果、租户隔离或自动研究闭环的验收结论。

## 验证与限制

新增 `platform/tests/harness_bridge_test.py` 使用模拟 Harness HTTP（不调用模型、Docker、Engine）验证 Session/管理员/代入权限、任务合同、失败脱敏和成果附件，运行：

```bash
platform/.venv/bin/python -m pytest -c platform/pyproject.toml -q platform/tests/harness_bridge_test.py
cd web && npm run lint && npm run test:frontend && npm run build
```

真实验收还需要在用户的开发主机上启动现有单 Harness Docker、配置测试模型 Key，执行任务和取消/续接/重启恢复，核实异常网络、任务超时和模型输出安全。当前阶段不可据此声明生产可用。

长期路线：Platform 持久化任务所有者/租户与审计；开发租户权限和资源范围；可信 Research Gateway 代持短期 Engine Grant；Agent SDK 工具经隔离 MCP Bridge 访问 Engine 只读数据与开发回测；结果进入审核/策略登记/独立准入边界，最终支持多轮假设发现、回测、归因和迭代。**不能把目前的管理员本地接口直接当作这一生产控制面。**
