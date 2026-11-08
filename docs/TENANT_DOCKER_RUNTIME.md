# 每租户独立 Docker Engine（第一阶段）

此阶段将正式/开发租户的 NautilusTrader Engine 放入**独立、常驻**的 Docker 容器。
Web/FastAPI、用户权限、业务数据库和策略商店不进入租户容器。
业务 API 继续通过原有 `EngineClient` 的 HTTP 契约访问 Engine，但 Docker 模式按
`tenant_id` 路由到不同的容器。容器本身不暴露给公网，也不持有 Docker socket。

本阶段**不实现**共享数据缓存、数据预加载、分布式 Worker 或 DeepSeek Harness。
每个开发租户有独立的 `data/`，需要自行准备所需历史数据；重复回测仍由现有
`run_backtest()` 逐次从本租户 Catalog 读取。

## 准备镜像

需要安装 Docker Engine、Docker CLI，并确保业务 API 的宿主机用户具有 Docker
操作权限（此权限等同于高权限，**不要**授予普通用户、策略代码或 Harness）。

```bash
docker build -f docker/engine.Dockerfile -t seealpha-engine:latest .
```

镜像包含 NautilusTrader / FastAPI、所需 Python 依赖和 release 版 Rust
`nautilus-data-converter`。部署新版本时重新构建镜像、按停机流程重建对应租户
容器；删除容器不会自动删除绑定的数据卷，删除租户时旧目录会进入 `.retired` 隔离区。

## 开启 Docker 部署

仅在已经完成旧 Engine 状态迁移或**全新部署**时开启。
保持现有 API 的数据库、会话和密钥配置不变，增加：

```bash
export ENGINE_DEPLOYMENT=docker
export ENGINE_DOCKER_IMAGE=seealpha-engine:latest
export ENGINE_TENANT_ROOT=/absolute/path/to/seealpha/.local/tenants
export ENGINE_PRODUCTION_CPUS=2
export ENGINE_PRODUCTION_MEMORY=4g
export ENGINE_DEVELOPMENT_CPUS=4
export ENGINE_DEVELOPMENT_MEMORY=8g
# 然后按 api/README.md 中的命令启动业务 API
```

以上只是默认配额示例，须结合机器容量和实际内存峰值调整。
`ENGINE_DEPLOYMENT=single`（默认）保留原来的单 Engine 行为，便于开发和回滚；
Docker 模式不在失败时偷偷回退到生产进程内执行。

首次 API 启动时会确保业务库里的活跃租户各有一个 `seealpha-engine-<tenant_id>`
容器。创建开发租户 / 新用户时也会部署容器。
开发租户归档会先导出 Engine 切片，然后停止并移除容器；恢复时从持久状态卷
重建容器并导入切片。删除租户会停止并移除容器，随后将其旧数据、日志、令牌和状态目录
**整体隔离保留**在 `.local/tenants/.retired/<tenant_id>-<随机标识>/`；
不会自动永久删除这些文件。这样即使业务数据库重新使用同一租户 ID，
新租户也不会继承旧用户的策略、行情或凭据。归档租户不移动目录，仍可恢复。

Docker 使用 `--cpus`、`--memory`、`--memory-swap`、`--pids-limit`，
并去掉 Linux capabilities、启用 `no-new-privileges`。
这是资源约束，并不是对任意不可信策略代码的完整安全沙箱；
目前仍应只加载受信任、经审核的策略包。
单机多个容器仍会共享宿主机 I/O；对低延迟生产有严格要求时需部署在独立主机。

## 存储和网络

普通租户的宿主机持久目录：

```text
.local/tenants/<tenant_id>/
  engine.env       # 0600, 此租户专属 Engine 令牌及固定身份
  state/           # Engine SQLite WAL 订单/成交/持仓/幂等事实
  data/            # 独立 raw、Catalog 和行情数据，保留在宿主机
  local/           # 容器内部 .local/run
  logs/            # 子进程日志
```

**例外：** 为保持已有行情管理页面、删除/裁剪任务与结果文件的路径兼容，
数据库标记为 `system_key=default` 的默认正式租户继续绑定宿主机项目
`data/`，以及 `.local/state/api/data_jobs/`。这是该租户自己的数据目录，
不被其他租户 Engine 共享。其他所有租户均绑定上述独立 `data/`。

开发租户的历史 Catalog 不会自动复制。准备策略回测前，应当在其独立数据目录
中生成或**复制属于该租户的数据**。如原始数据保存在项目 `data/catalog`，
在授权使用的前提下，可在关闭有关读写任务时手动复制到
`.local/tenants/<tenant_id>/data/catalog`。没有该数据时，现有回测会继续
明确报“指定区间没有可用的本地 K 线”，不会回退到另一个租户的 Catalog。

每个 Engine 内部仍监听 `8766`，Docker 仅把它映射到宿主机
`127.0.0.1` 随机端口，由 API 发现。各租户使用独立 Bearer 令牌，
Engine 还校验 URL 里的租户 ID 和登记用途。不要在外部反向代理或防火墙上
公开这些随机端口，也不要将 Engine 令牌交给浏览器。
现在 Docker 管理只支持**同一 Docker 宿主机**；跨服务器部署需要另行加入
可信 Worker Agent / 节点路由，而不是暴露 Docker daemon TCP 端口。

## 旧 Engine 迁移与回滚（必须先做）

仅切换 `ENGINE_DEPLOYMENT` **不会搬迁**原
`.local/state/engine/book.sqlite` 中的订单幂等、成交、持仓、缺口与事件。
对已经有生产记录的部署，禁止直接开启 Docker 模式，否则新租户状态卷会从空库启动。

先安排交易停机窗口，停止旧 API、旧 Engine 及其行情子进程，备份业务库、
`.local/state/engine/` 和 `data/`；由运维在离线环境使用 Engine 已有的
`export_tenant()/import_tenant()` 将每个租户状态迁移到独立容器，
核对订单请求 ID、持仓、成交、策略下发、缺口与订阅数量后再切换 API。
**本 PR 不会自动迁移或删除旧状态，且不会自动启动实盘策略。**
迁移失败时保持旧 API/Engine 停机、保留备份，不应把两个 Engine 同时连接到同一个真实账户。

如需回滚到 `ENGINE_DEPLOYMENT=single`，必须先停止 Docker 容器；
在 Docker 模式下新产生的 Engine 事实也必须先反向迁移，不能直接使用旧 SQLite，
否则会遗漏幂等请求和成交证据。

## 测试

```bash
uv run ruff check api/config.py api/main.py api/engine_client.py api/tenant_docker.py \
  api/auth/users.py engine/config.py engine/app.py tests/api/tenant_docker_test.py
uv run pytest -q tests/api/tenant_docker_test.py tests/api/tenants_test.py \
  tests/api/backtest_test.py tests/api/user_tenants_test.py \
  tests/engine/phase1_test.py tests/engine/backtest_test.py
```

PR CI 还包含 Docker 镜像构建及租户身份绑定的 HTTP 冒烟检查。
