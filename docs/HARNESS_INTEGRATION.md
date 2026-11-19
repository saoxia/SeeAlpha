# 在 SeeAlpha Platform 接入独立 Harness（本地开发阶段）

> **2026-11-19 架构调整**：已选定 Harness → Platform 轻量研究代理 → Engine Research API，不再要求独立 Gateway/MCP Bridge。详见 [简化 AI 研究调用链](DIRECT_AI_RESEARCH.md)。现有 Harness 任务/Session 入口保持不变；新增 Platform 内部 `DirectEngineResearchClient` 但任务级代理授权与 Agent 调用尚未接通，不能称 AI 自动回测已完成。

> 本文描述原有单用户 `HARNESS_DEPLOYMENT=single` 模式。新增的可选 `per_user` 实例部署、远端 Node Agent 和用户级路由见 [每用户独立 Harness](HARNESS_PER_USER.md)。两种模式均尚未获得真实资金或生产级 Agent 沙箱验收。

> 范围：**独立 `saoxia/seealpha-harness` 常驻服务** → SeeAlpha Platform API → AI Harness。不会嵌入独立 Harness 运行时代码、安装 DeepSeek SDK 到 Platform，也不会替换独立 Engine 或创建每任务 Docker。

## 架构和当前资格

```text
登录用户 → SeeAlpha Web /app → AI Harness → 同源 /api/harness/*
          → Platform Session 与现阶段单用户开发桥接权限检查
          → 固定来源 Harness HTTP v1（Bearer 只在服务器）
          → 唯一常驻 Harness Docker → 顺序队列 / 每任务独立 Worker 进程
```

**产品归属：AI Harness 是普通用户研究工作台，不属于管理后台。** 当前服务器桥接仍是单用户开发模式：默认关闭；可通过 `HARNESS_OWNER_USERNAME` 指定一名受信任的普通用户使用，管理员不会因管理员身份自动获得该用户的 Harness 权限。出于原有单用户部署兼容性，未设置时仍由既有 `UPDATE_ADMIN_USERNAME` 账户使用。不得开放给多个不可信用户，这不是最终多用户访问策略。当前运行时不是生产多租户 Agent 沙箱：Harness 任务内的 DeepSeek SDK 自由 Shell 与控制器仍属于单容器/同 UID；不能保证租户工作区间强隔离，模型测试凭据可能被 Agent 读取。禁止把生产交易密钥、共享租户私有行情或生产 Engine 凭据交给 Agent。选定的 Platform 轻量研究代理仍需接入任务级 Grant、Agent 命令鉴权、隔离与 Engine 端到端回测授权；旧 Gateway/MCP 不再是前置要求。

本阶段可以在 AI Harness 页提交任意本地 AI 研究提示、查询任务状态和安全进度事件、取消、打开历史 Session、在已完成 Session 上继续、阅读最终回答、显式登记 `exports/` 中的研究成果并下载。**不声称已经能通过 Agent 直接运行 Engine 开发回测。**

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

Harness 镜像中的运行用户是 UID 10001。Linux 首次使用宿主路径 `./.local/state:/state` 绑定卷前，应确认已有状态备份并检查该路径可由 UID 10001 读写；新安装可以在 Harness 仓库执行 `sudo install -d -m 700 -o 10001 -g 10001 .local/state`。不应为绕过权限故障改用 root 运行容器或对已有目录执行不受控递归覆盖。

Harness 默认只监听宿主 `127.0.0.1:8777`，不要将其 API 端口暴露到公网、共享 LAN 或不可信 Agent 网络。SeeAlpha Platform 和 Harness 在同一宿主运行时，在**Platform API 进程**设置：

```bash
export HARNESS_ENABLED=true
export HARNESS_URL=http://127.0.0.1:8777
export HARNESS_OWNER_USERNAME=\'可信的研究用户名\' # 当前唯一受信任研究用户，非管理员角色\nexport HARNESS_API_TOKEN='<与 Harness .env 中完全一致的高强度密钥>'
```

`HARNESS_URL` 是**固定服务根地址**，不支持在请求参数指定 URL。若 Platform API 自身运行在另一容器或主机内，`127.0.0.1` 将指向该进程的网络命名空间；此时须配置可信内部可达地址，保持 Harness 管理 API 仅对可信 Platform 开放。两端之间应有真实网络边界，不能将 Harness 的控制 Token 注入不可信 Agent 工作区。

切换 `HARNESS_OWNER_USERNAME` 前必须为新研究用户部署**全新的 Harness 状态目录与凭据**，不要把已有任务历史、Agent home、模型凭据直接移交给另一账号。该设置只是单用户托管的受信任账号选择，**不能替代同容器多租户隔离**。\n\n未设置 `HARNESS_ENABLED=true` 时，SeeAlpha 原来的登录、行情、回测、交易及管理 API 不依赖 Harness，也不受其断连影响。

## Web 操作

打开 SeeAlpha → /app → AI Harness；目前在单用户本地开发验证会话下，状态应为「Harness 已连接」。输入研究目标后点击「开始研究」。请求含 `queue_if_busy: true`：常驻 Harness 运行中时任务进入其有界 FIFO 队列，不会启动第二个 Docker。任务状态每两秒通过 Platform API 查询，完成后展示最终回答及已登记成果清单。点击成果名称经 Platform 强制附件下载；主动创建新会话不会删除 Harness 服务端记录。Harness 保留历史任务索引；当前浏览器另外记住最近访问的任务 ID，历史列表不含原始 Prompt。Harness 仍是单用户本地服务，不具备跨用户/租户历史隔离。

如果任务失败、中断或取消，重新提交将创建新任务；只有完成的 Session 可以续接。断开网络、提交超时等未知结果不会被自动重发，以避免重复运行。Harness 容器重启后，任务恢复与排队门禁由 Harness 自身 `TaskManager` 管理，Platform 不自动向 `/v1/queue/resume` 发控制命令。

## 安全与接口

- FastAPI 路由：`GET /api/harness/status`；`GET /api/harness/tasks`；`POST /api/harness/tasks`；`GET /api/harness/tasks/{id}`；`GET /api/harness/tasks/{id}/timeline?after=N`；`POST /api/harness/tasks/{id}/continue`；`POST /api/harness/tasks/{id}/cancel`；`GET /api/harness/tasks/{id}/artifacts`；`POST /api/harness/tasks/{id}/artifacts/import`；`GET /api/harness/tasks/{id}/artifacts/{artifact_id}`。
- 所有路由经真实 SeeAlpha Session 身份校验。目前桥接拒绝未登录、非指定账户与管理员代入其他用户的会话；当 `HARNESS_OWNER_USERNAME` 设置为普通用户时，该用户可以提交和查询自己的单用户 Harness 任务。不得让不同用户共享同一运行实例；在完成任务所有权、租户隔离、Worker 进程安全边界与审计前，不得将当前单用户判断改为任意登录用户可访问。既有同源写操作来源检查不变。
- 只有 Platform 服务端传递 `Authorization: Bearer` 和版本 `X-SeeAlpha-Harness-Contract-Version: 1`。禁止任意 URL、通用 HTTP 转发、跳转及透传上游鉴权错误。API Token 不会进入浏览器、成果、任务 Payload 或日志。
- Artifact 仅以 `application/octet-stream`、`Content-Disposition: attachment` 和 `nosniff` 交付，限制 16 MiB；不将未审查 HTML/JS 作为主站内容执行。
- 研究过程不会向 Engine 下单或修改风控；不存在生产 Grant、真实回测结果、租户隔离或自动研究闭环的验收结论。

## 验证与限制

新增 `platform/tests/harness_bridge_test.py` 使用模拟 Harness HTTP（不调用模型、Docker、Engine）验证 Session/管理员/代入权限、任务历史、进度和成果登记/下载。专用 `Harness research workspace checks` CI 独立运行前后端测试，避免被既有 Engine 全链路回归中的无关失败跳过。手工执行：

```bash
platform/.venv/bin/python -m pytest -c platform/pyproject.toml -q platform/tests/harness_bridge_test.py
cd web && npm run lint && npm run test:frontend && npm run build
```

可执行真实模型的开发机上，先设置 Platform API 进程环境变量、启动已存在的 Harness 容器及 Platform API，然后在 Platform 仓库根目录运行：

```bash
platform/.venv/bin/python scripts/verify_harness_live.py --origin http://127.0.0.1:8000 --username admin
```

脚本交互读取管理员密码（不会写入命令行或日志），通过**真实 Platform 登录 Cookie**调用 Harness，实际提交一轮 Python 研究任务、等待完成、核实历史和进度、验证成果附件 SHA-256，然后续接第二轮任务再次验证摘要。它**不**使用假模型或模拟最终结果；模型 Key、Harness/Platform 是否实际部署均必须由当前开发机提供。任务调用产生真实模型成本。脚本不会接触 Engine、交易账户或下单；网络中断后不会自动重发。

真实验收还需要在用户的开发主机上启动现有单 Harness Docker、配置测试模型 Key，执行任务和取消/续接/重启恢复，核实异常网络、任务超时和模型输出安全。当前阶段不可据此声明生产可用。

长期路线：Platform 持久化任务所有者/租户与审计；开发租户权限和资源范围；Platform 内部轻量代理代持短期 Engine Grant，向 Agent 提供经任务授权的固定研究命令访问 Engine 开发数据与回测；结果进入审核/策略登记/独立准入边界，最终支持多轮假设发现、回测、归因和迭代。**不能把目前的管理员本地接口直接当作这一生产控制面。**
