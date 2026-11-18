# SeeAlpha 本机安装（Linux / macOS 原生，Windows 通过 WSL2）

这是面向 **macOS / Linux 单机研究与开发** 的隔离安装入口，Windows 通过 WSL2 Ubuntu 复用 Linux 入口；非多用户或生产发行包。
不会连接既有 PostgreSQL、迁移生产数据库、自动更新交易引擎、配置交易所凭据或开启实盘。
Engine 和 Platform 仍是不同仓库、不同 Python 环境及不同进程。

## 安装前提

- Git、[uv](https://docs.astral.sh/uv/)、Node.js **>= 22.13.0**、npm。
- 能访问 GitHub、Python 与 npm 依赖源，并有足够磁盘空间。
- 端口 `127.0.0.1:8766`、`:8000`、`:3000` 未被占用。
- macOS / Linux 原生运行。Windows 需要 Windows 10 2004（Build 19041）及以上或 Windows 11，并运行 WSL2 Ubuntu；不支持 Windows 原生 Engine 或 `.dmg` 安装程序。
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


## Windows：WSL2 Ubuntu 引导入口

Windows 仅承担 WSL2 状态检查、Ubuntu 安装引导以及将 SeeAlpha 命令转发给 Linux；
Engine / API / Web 实际运行在 WSL2 的 Linux 环境内。不会将 Windows 的挂载目录
`/mnt/c` 用作 SQLite、Python 虚拟环境或行情缓存的默认位置。

先从可信仓库获取 **完整 SeeAlpha 源码**（Windows 需要 Git，或下载仓库 ZIP 并解压）。
在 Windows PowerShell、仓库根目录执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\seealpha.ps1 install
```

执行策略例外仅对这次 PowerShell 进程生效；运行前应核对所下载脚本的来源和内容。

- 已有**唯一**的 Ubuntu WSL2：直接复用；即使该发行版已停止也会在调用 Linux 命令时启动。
- 已有多个 Ubuntu WSL2：明确指定 `-Distro Ubuntu-24.04`（按实际安装名称替换），避免使用错误的开发环境。
- 尚无 Ubuntu WSL2：向用户确认后，由 UAC 提权执行微软官方 `wsl.exe --install --distribution Ubuntu`。安装可能要求重启 Windows，然后**首次启动 Ubuntu 创建普通 Linux 用户**，最后重新运行 `install`。
- 只有 WSL1：拒绝自动转换，避免修改已有发行版、文件和工作状态；需用户自行备份、转换或另装独立的 WSL2 Ubuntu。
- 不会自动重启 Windows，不会改动其他发行版、WSL 默认发行版或执行 `wsl --shutdown`。

首次成功进入 WSL2 后，Windows 启动器会克隆 SeeAlpha 至 Linux 用户目录
`~/seealpha`，并复用 `./seealpha install`。已有该目录时必须是来自 SeeAlpha 的
Git 仓库，脚本既不覆盖已有文件，也不自动执行 `git pull`。若 Ubuntu 内缺少 Git、
uv 或 Node.js 22.13+ / npm，安装脚本会报告缺少的工具，需要在 **Ubuntu 内**
按以下官方说明安装，然后重试：

- Git：Ubuntu 内执行 `sudo apt-get update && sudo apt-get install -y git`。
- uv：[官方安装指南](https://docs.astral.sh/uv/getting-started/installation/)。
- Node.js：[官方 Linux 下载与安装说明](https://nodejs.org/en/download)，需要 >= 22.13.0。

Windows PowerShell 后续管理（在 Windows 仓库根目录）：

```powershell
.\seealpha.ps1 admin create admin
.\seealpha.ps1 start
.\seealpha.ps1 status
.\seealpha.ps1 doctor
.\seealpha.ps1 stop
```

如 PowerShell 限制本地脚本，请使用上面的 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\seealpha.ps1 <命令>` 形式。新系统内创建管理员密码需要交互输入；非交互安装不会保存默认密码。

打开 Windows 浏览器访问 <http://127.0.0.1:3000>。此方式依赖 WSL2 的
localhost 转发；被 Windows 防火墙、代理或自定义 `.wslconfig` 干扰时可能需要
单独诊断网络。**状态为 stopped 不代表 WSL 没安装**，启动器会自动拉起它。

注意：Windows 启动器目前只能引导 WSL 和复用 Linux 安装器，
**不自动安装完整 Linux 开发工具链**。Windows 的 WSL2 实际端到端安装
需使用有虚拟化能力的真实 Windows 机器验收；GitHub Windows runner 只验证脚本和选择逻辑。

官方依据：[安装 WSL](https://learn.microsoft.com/zh-cn/windows/wsl/install)、
[WSL 命令](https://learn.microsoft.com/zh-cn/windows/wsl/basic-commands)、
[本地网络访问](https://learn.microsoft.com/zh-cn/windows/wsl/networking)。
