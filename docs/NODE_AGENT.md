# SeeAlpha 轻量 Node Agent（第一阶段）

Agent 与 Platform 共用 **saoxia/SeeAlpha** 源码，不创建第三个仓库。只启动 Agent
时不会导入业务 API、ORM、Web 或 NautilusTrader。Agent 不理解租户、Engine、回测
或行情业务；它仅执行授权的通用 Docker 工作负载操作。

## 能力与明确限制

- Platform 仍是唯一业务入口，节点只通过出站 HTTPS POST 长轮询连接，不要求公网 IP 或开放 Agent 入站端口。
- 容器镜像必须在节点本地允许列表内；资源、绑定卷、端口及 Docker 操作受约束。
- 支持通用工作负载的 ensure / stop / status / request 操作。新增业务容器无需修改 Agent。
- `ENGINE_DEPLOYMENT=distributed` 时，未绑定的租户仍使用现有**本机** Docker 管理器。
- **远端只接受 development 租户；默认行情租户、正式租户必须留在本地。**
- 租户到节点的绑定是管理员维护的静态配置；切换绑定并不迁移状态、数据或账户。
- 初版使用单 Platform API 进程的内存命令队列。**不支持多个 API Worker、水平扩容、跨 API 重启的命令恢复。** 超时操作执行结果不确定，禁止盲目重试实盘指令。
- Engine 的普通 JSON HTTP v2 API 通过出站节点连接转发；**流式导出、流式交易流水和本地文件日志路径不支持远程转发**。请求响应最大 12 MiB。开发租户应使用异步回测接口。
- 不提供无审核策略执行沙箱，不支持实盘自动切换、容灾接管、跨节点数据迁移或容器镜像热更新。
- Agent 所在服务器对本地 Docker 具有高权限；仅应运行可信镜像并保护节点凭据。配置 TLS、隔离部署用户和专用 Docker 主机。

## 启动（同一个仓库，不同角色）

安装 Platform 的锁定轻依赖（Agent 不安装 Engine / NautilusTrader）：

```bash
uv sync --project platform --locked --no-default-groups
```

可选服务入口：

```bash
platform/.venv/bin/python -m node --role platform --host 127.0.0.1 --port 8000
platform/.venv/bin/python -m node --role agent
platform/.venv/bin/python -m node --role all --host 127.0.0.1 --port 8000
```

`platform` 使用正常 Platform 配置；`all` 在未明确设置
`ENGINE_DEPLOYMENT` 时启用原有本地 Docker 模式。原有
`uvicorn api.main:app` 启动方式继续有效。

## Platform 配置（仅部署新开发租户）

在主服务器保存私有节点注册与租户绑定文件，例如
`/srv/seealpha/private/nodes.json`：

```json
{
  "nodes": {
    "edge-a": "<至少32位的随机私有节点令牌>"
  },
  "tenant_nodes": {
    "7": "edge-a"
  }
}
```

令牌可使用 `python -c 'import secrets; print(secrets.token_urlsafe(48))'` 生成。
将文件设为 `chmod 600`，不得提交 Git。节点及租户编号必须是已批准的值。

```bash
export ENGINE_DEPLOYMENT=distributed
export NODE_CONFIG_FILE=/srv/seealpha/private/nodes.json
export ENGINE_DOCKER_IMAGE=seealpha-engine:<经过验证的固定版本>
export ENGINE_TENANT_ROOT=/srv/seealpha/tenants
# 还需要现有 DATABASE_URL、ALLOWED_HOSTS、Engine 相关配置
platform/.venv/bin/python -m node --role platform
```

请仅在一个 Platform API Worker 中运行分布式控制功能。配置文件在 Platform
启动时加载；修改分配后必须重新启动单个 Platform API 进程。**不要把已有本地
Engine 的租户 ID 直接指向远端节点**：它会创建一份新的状态卷，必须先人工备份、
确认归属和执行受控迁移。暂不自动搬迁生产账户。

管理端登录后可以查询 `GET /api/nodes` 获取在线节点和显式绑定（不返回令牌）。

## 远端 Agent 配置

在远端服务器安装 Docker Engine 和 Docker CLI，并从同仓库安装轻依赖。
创建受限用户和独立的工作负载根目录，设置：

```bash
export NODE_PLATFORM_URL=https://your-seealpha-domain.example
export NODE_ID=edge-a
export NODE_TOKEN='<与主服务器 nodes.json 对应的私有令牌>'
export NODE_ALLOWED_IMAGES=seealpha-engine:<与Platform固定版本相同>
export NODE_ROOT=/srv/seealpha/node-workloads
export NODE_MAX_CONTAINERS=8
export NODE_MAX_CPUS=8
export NODE_MAX_MEMORY=16g
platform/.venv/bin/python -m node --role agent
```

私有 NODE_TOKEN 应通过受限的环境文件或服务管理器提供，不要写入 shell 历史或
公开日志。平台 URL 必须是证书有效的 HTTPS；仅 localhost / 127.0.0.1
测试可使用 HTTP。Agent 会周期性轮询、上报在线状态，远端没有监听端口。

Node Agent 只接受固定镜像集合、指定的 CPU/内存上限和
`NODE_MAX_CONTAINERS` 容器数量。宿主机卷只能在 `NODE_ROOT` 内创建，
容器挂载点必须位于 `/app/` 下，服务端口仅绑定本机
`127.0.0.1`。节点不会开放 Docker daemon TCP 接口。

## 运行与验证

```bash
platform/.venv/bin/ruff check node api/distributed_docker.py api/node_routes.py api/engine_client.py api/main.py api/config.py platform/tests/node_agent_test.py
platform/.venv/bin/python -m pytest -c platform/pyproject.toml -q platform/tests/node_agent_test.py platform/tests/tenant_docker_test.py platform/tests/engine_outage_test.py
```

确认：节点认证失败会返回 401，不能由一个节点领取另一个节点的指令，
Agent 断线时 Platform 返回不可用，不会在主程序暗中执行远程工作负载；
重新连接后使用相同工作负载定义时具备幂等 ensure。生产场景还需在隔离
Docker 主机执行端到端 Engine HTTP、回测与磁盘恢复验收。
