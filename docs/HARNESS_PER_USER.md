# 每用户独立 Harness（可选部署模式）

SeeAlpha 支持通过现有 Docker Workload / Node Agent 接口，将一位用户映射到一个独立的常驻 Harness 实例。**该模式默认关闭**；单实例 `HARNESS_URL` 模式可用于受限的单用户测试。每个用户持有独立容器、持久化状态卷、Session 历史和 Harness HTTP 控制令牌；Harness 内部仍运行单用户 TaskManager。

## 本机部署

先从 [seealpha-harness](https://github.com/saoxia/seealpha-harness) 的已审核固定提交构建受信任镜像，例如：

```bash
docker build -t seealpha-harness:approved-20261119 /path/to/seealpha-harness
```

使用拥有 Docker 操作权限的**非 root** 专用 SeeAlpha Platform 系统用户，准备仅该用户可访问的私有目录（权限 0700），并配置：

```bash
export HARNESS_ENABLED=true
export HARNESS_DEPLOYMENT=per_user
export HARNESS_DOCKER_IMAGE=seealpha-harness:approved-20261119
export HARNESS_ROOT=/srv/seealpha/private/harness-users
export HARNESS_CPUS=2
export HARNESS_MEMORY=4g
```

镜像标签必须对应运维确认的固定版本，不允许 `latest`；示例标签不是官方已验收的发布镜像。启动 Platform API 后，用户首次打开 AI Harness 时按其登录 ID 创建（或恢复）`seealpha-node-harness-user-<user_id>`；相同用户的后续请求复用该容器。实例持久化数据保存在 `HARNESS_ROOT/workloads/harness-user-<id>/volumes/state`，专属 Harness 控制 Token 位于 `HARNESS_ROOT/credentials/user-<id>.token`（0600）。**不得删除状态目录来处理启动失败。**

实例使用资源限额、loopback HTTP 端口、非 root 运行身份、只读根文件系统和受约束的状态挂载。本机最多管理 32 个此类 Harness 工作负载；超额时会拒绝新实例，不清理已有用户数据。不会为每个研究任务额外启动 Docker。管理员账号不能执行任何普通用户的 Harness 任务。

## 远端 Node Agent

沿用 [Node Agent 配置](NODE_AGENT.md)，不创建新的节点程序。Platform 的 `NODE_CONFIG_FILE`（0600）可新增独立的用户到节点绑定：

```json
{
  "nodes": {"edge-a": "<至少32位的随机节点令牌>"},
  "tenant_nodes": {},
  "harness_user_nodes": {"7": "edge-a", "8": "local"}
}
```

* `harness_user_nodes` 使用 **SeeAlpha 用户 ID**，不是租户 ID；省略绑定意味着运行在本机。绑定文件由管理员维护，切换节点**不会迁移 Session、研究文件或容器状态**。
* Node Agent 的 `NODE_ALLOWED_IMAGES` 要包含相同固定 Harness 镜像。远端节点要预先加载镜像、由非 root 的受限运行用户执行 Agent，并为 `NODE_ROOT` 配置独立的私有工作目录。Docker Runtime 的 `NODE_MAX_CONTAINERS`、CPU 与内存上限仍然生效。Agent 通过现有出站轮询、`ensure` 和 `request` 管理 Harness，无需 Agent 入站端口。
* Platform 和远端 Node Agent 之间使用已验证证书的 HTTPS。远端 Harness 只映射节点的 `127.0.0.1` 随机端口，由已认证的 Node Agent 代为转发。
* 普通请求通过原 Node Agent JSON 转发，成果下载增加有界 Base64 转发。**目前远端单文件下载上限 8 MiB**，超过上限返回不可用；本机仍沿用原 Harness 的 16 MiB 限制。远端流式 SSE 尚未纳入 Node Agent 中继，Web 当前使用任务进度轮询。

只有单个 Platform API Worker 可以管理远端 Node Hub：初版采用进程内命令队列，不提供跨 Worker 调度、自动故障切换、迁移或容器镜像滚动升级。

## 模型 Key 与安全边界

**本次不注入任何第三方模型 API Key。** 新实例只有随机 Harness 服务控制令牌，`/health` 会报告模型尚未配置；在独立模型配置与可信调用代理接入前，用户不能完成真实模型研究任务。后续模型凭据应由 Platform 的可信代理代持，绝不能把管理员或其他用户的原始 API Key 注入具有任意 Shell 能力的 Harness Worker。

独立 Docker 主要隔离用户的任务、Session 与文件系统挂载，不等于同宿主机上的强沙箱；宿主机权限、网络出站策略、Docker 安全边界仍需要在实际环境验收。此模式不授予 Harness 真实交易、Engine 管理或生产密钥访问权限。

## 验证

```bash
platform/.venv/bin/ruff check api/node api/research/harness_instances.py api/research/harness_router.py platform/tests/harness_instances_test.py platform/tests/node_agent_test.py
platform/.venv/bin/python -m pytest -c platform/pyproject.toml -q platform/tests/harness_bridge_test.py platform/tests/harness_instances_test.py platform/tests/node_agent_test.py
```

单元测试不启动 Docker、不调用模型。完整部署还需要使用受限系统用户、真实 Docker 和远端 Node Agent 验证首次创建、重复访问、重启恢复、历史数据持久化、节点掉线及授权拒绝。**目前不能宣称真实服务器上的端到端验收已经完成。**
