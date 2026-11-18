# SeeAlpha 本机安装（第一批）

这是面向 **macOS / Linux 单机研究与开发** 的隔离安装入口，非多用户或生产发行包。
不会连接既有 PostgreSQL、迁移生产数据库、自动更新交易引擎、配置交易所凭据或开启实盘。
Engine 和 Platform 仍是不同仓库、不同 Python 环境及不同进程。

## 安装前提

- Git、[uv](https://docs.astral.sh/uv/)、Node.js **>= 22.13.0**、npm。
- 能访问 GitHub、Python 与 npm 依赖源，并有足够磁盘空间。
- 端口 `127.0.0.1:8766`、`:8000`、`:3000` 未被占用。
- macOS / Linux。本阶段未适配 Windows 原生命令或 `.dmg` 安装程序。
- `uv` 按各自的 `.python-version` 管理 Python；不需要手工创建两个虚拟环境。

在 **SeeAlpha Platform 仓库根目录**执行：

```bash
./seealpha install
```

安装脚本会：
1. 拒绝继承 `DATABASE_URL`、`DATABASE_URL_FILE`、Engine 运行配置等可能指向现有部署的变量。
2. 通过 HTTPS 检出固定提交的 [seealpha-engine](https://github.com/saoxia/seealpha-engine)，校验 Engine / Platform 共享契约。
3. 分别执行两个仓库锁定依赖的 `uv sync`；在 `web/` 执行 `npm ci` 和 `npm run build`。
4. 仅对**不存在**的本机 `.local/state/api/seealpha.sqlite` 执行 Alembic 初始化；为 API / Engine 生成私有本地令牌。
5. 首次交互安装时调用现有管理员管理命令输入密码。非交互安装需随后单独创建管理员。

> Engine 仍从源码依赖安装，第一批没有预构建二进制和 Rust Release 转换器打包；数据转换能力和性能需单独验收。第二批计划改为分发固定版本的预构建镜像。

安装完成：

```bash
./seealpha admin create admin  # 如果首次安装时尚未创建
./seealpha start
./seealpha status
./seealpha doctor
./seealpha stop
```

浏览器访问 <http://127.0.0.1:3000>。本机 HTTP 模式设置 `COOKIE_SECURE=false`，**只能使用 loopback 地址**，不可把开发服务开放到局域网或公网。

`./seealpha admin reset admin` 可以通过交互式输入重置管理员密码。密码不会以参数方式传递。重复运行 `admin create` 不会覆盖已存在的账户。

## 数据及进程保护

- 数据库：`.local/state/api/seealpha.sqlite`
- 本机私有配置：`.local/config/api/database_url`、`engine_token`（权限 600）
- 固定版本 Engine 源码与运行态：`.local/engine-src/`
- 运行日志：`.local/logs/seealpha/`
- 启动器 PID：`.local/run/seealpha/`
- 前端配置：`web/.env.local`（现有配置保留，不覆盖）

这些路径均被 `.gitignore` 忽略。`stop` 只向经 PID + 命令行验证为本启动器创建的进程发送 `SIGTERM`，不会删除订单、行情、数据库或容器，不会强制 `SIGKILL`。

再次运行 `install` 时不会重新执行已有数据库的迁移；如现有 SQLite 的 Alembic revision 与代码不一致，会失败并要求人工处理。不要删除旧数据库“修复”安装，先备份及确认数据归属。

若安装中断后留下不完整 Engine checkout、SQLite 或配置，脚本可能拒绝重试。应检查日志和已有数据，确认可以恢复后再处理；安装器不自动擦除不明状态。

## 与现有正式部署的关系

原 `api/README.md`、`docs/EXTERNAL_ENGINE.md`、`docs/TENANT_DOCKER_RUNTIME.md` 仍是多租户 / PostgreSQL / Docker 部署规范。本命令不取代它们、不迁移正式租户或真实账户状态。需要持续运行、服务器部署、自动备份及 Engine 发布更新时，继续执行单独的生产验收和停机迁移流程。

首次安装在空数据库上运行，不配置任何交易所账号，不会主动下达订单；但**用户以后若在管理端配置交易账号和运行任务，重启现有服务可能恢复这些任务**。因此本机安装不等于交易执行安全验收。
