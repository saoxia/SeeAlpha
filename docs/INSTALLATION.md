# SeeAlpha 本机安装（Linux / macOS 原生，Windows 通过 WSL2）

这是面向 **macOS / Linux 单机研究与开发** 的隔离安装入口，Windows 通过 WSL2 Ubuntu 复用 Linux 入口；非多用户或生产发行包。
不会连接既有 PostgreSQL、迁移生产数据库、自动更新交易引擎、配置交易所凭据或开启实盘。
Engine 和 Platform 仍是不同仓库、不同 Python 环境及不同进程。

## 安装前提

- Git、curl、tar、SHA256 校验工具（sha256sum 或 shasum）；Linux ARM/x64、macOS ARM/x64 支持项目内自动安装 uv 和 Node.js/npm。
- 若禁用自动引导（`--no-bootstrap`），则必须提前安装 [uv](https://docs.astral.sh/uv/) 与 Node.js **>= 22.13.0** / npm。
- 能访问 GitHub、Python 与 npm 依赖源，并有足够磁盘空间。
- 端口 `127.0.0.1:8766`、`:8000`、`:3000` 未被占用。
- macOS / Linux 原生运行。Windows 需要 Windows 10 2004（Build 19041）及以上或 Windows 11，并运行 WSL2 Ubuntu；不支持 Windows 原生 Engine 或 `.dmg` 安装程序。
- `uv` 按各自的 `.python-version` 管理 Python；不需要手工创建两个虚拟环境。

在 **SeeAlpha Platform 仓库根目录**执行：

```bash
./seealpha up
```

`up` 首次执行时安装并启动，后续执行时在全部进程已运行时只显示状态；存在部分服务运行或端口冲突时拒绝隐式接管。也可用 `./seealpha install` 和 `./seealpha start` 分步执行。自动工具安装**仅写入当前项目的 `.local/`**，不调用 sudo，不覆盖系统 uv/Node；镜像部署则可完全跳过本机 Python/Node/Rust 构建。

安装脚本会：
1. 拒绝继承 `DATABASE_URL`、`DATABASE_URL_FILE`、Engine 运行配置等可能指向现有部署的变量，并校验固定版本下载的 SHA256；若缺少 uv/Node 则安装到项目私有目录。
2. 通过 HTTPS 检出固定提交的 [seealpha-engine](https://github.com/saoxia/seealpha-engine)，校验 Engine / Platform 共享契约。
3. 分别执行两个仓库锁定依赖的 `uv sync`；在 `web/` 执行 `npm ci` 和 `npm run build`。
4. 仅对**不存在**的本机 `.local/state/api/seealpha.sqlite` 执行 Alembic 初始化；为 API / Engine 生成私有本地令牌。
5. 首次交互安装时调用现有管理员管理命令输入密码。非交互安装需随后单独创建管理员。

> 源码安装仍使用 Engine Python 依赖与本机工具环境（独立 Rust 数据转换组件是否就绪需要额外验证）。另提供带有 Rust release 转换器的预构建 Docker 镜像部署；只有发布工作流成功并真正发布相应标签后才可拉取。

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

这些路径均被 `.gitignore` 忽略。`stop` 只向 PID 与记录的进程启动时间匹配的进程发送（旧单行 PID 记录仍需严格命令行验证） `SIGTERM`，不会删除订单、行情、数据库或容器，不会强制 `SIGKILL`。

再次运行 `install` 时不会重新执行已有数据库的迁移；如现有 SQLite 的 Alembic revision 与代码不一致，会失败并要求人工处理。不要删除旧数据库“修复”安装，先备份及确认数据归属。

安装中断后可重新运行 `./seealpha up` 或 `./seealpha install`。下载中的 Engine 位于 `.local/engine-src.partial`，仅在来源和工作区可核对时继续并原子发布；已发布的 Engine 不会自动变更。对尚无业务表的空 SQLite 可重新初始化；含未知表、部分迁移或不匹配 revision 的数据库仍会拒绝接管，不会自动擦除。

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
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\seealpha.ps1 up
```

执行策略例外仅对这次 PowerShell 进程生效；运行前应核对所下载脚本的来源和内容。

- 已有**唯一**的 Ubuntu WSL2：直接复用；即使该发行版已停止也会在调用 Linux 命令时启动。
- 已有多个 Ubuntu WSL2：明确指定 `-Distro Ubuntu-24.04`（按实际安装名称替换），避免使用错误的开发环境。
- 尚无 Ubuntu WSL2：向用户确认后，由 UAC 提权执行微软官方 `wsl.exe --install --distribution Ubuntu`。安装可能要求重启 Windows，然后**首次启动 Ubuntu 创建普通 Linux 用户**，最后重新运行 `install`。
- 只有 WSL1：拒绝自动转换，避免修改已有发行版、文件和工作状态；需用户自行备份、转换或另装独立的 WSL2 Ubuntu。
- 不会自动重启 Windows，不会改动其他发行版、WSL 默认发行版或执行 `wsl --shutdown`。

首次成功进入 WSL2 后，Windows 启动器会克隆 SeeAlpha 至 Linux 用户目录
`~/seealpha`，并复用 `./seealpha install`。已有该目录时必须是来自 SeeAlpha 的
Git 仓库，脚本既不覆盖已有文件，也不自动执行 `git pull`。若 Ubuntu 内缺少 Git、curl、xz-utils 等系统基础工具，Windows 引导器会先询问许可，再通过 `sudo apt-get` 安装；传入 `-Yes` 表示事先授权这一步（也适用于新装 WSL 的授权）。uv 和 Node.js/npm 在项目私有目录自动下载、校验和安装。网络、sudo 或 Ubuntu 包管理器不可用时会明确失败，不会静默更改其它发行版。

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
**会在获得授权后安装必要的 Ubuntu 基础工具，并在项目内安装 uv/Node.js**。Windows 的 WSL2 实际端到端安装
需使用有虚拟化能力的真实 Windows 机器验收；GitHub Windows runner 只验证脚本和选择逻辑。

官方依据：[安装 WSL](https://learn.microsoft.com/zh-cn/windows/wsl/install)、
[WSL 命令](https://learn.microsoft.com/zh-cn/windows/wsl/basic-commands)、
[本地网络访问](https://learn.microsoft.com/zh-cn/windows/wsl/networking)。

## 预构建镜像部署

预构建模式使用 Docker Engine/Desktop + Docker Compose v2，省去本机 uv、Node.js、NautilusTrader 与 Rust 的构建。CI 从统一固定的 Engine 完整提交 SHA 构建三个镜像（Engine、Platform、Web），先检查契约，再进行 Compose 冒烟测试，通过后才按版本标签发布到 GHCR。版本发布由 [release-images.yml](../.github/workflows/release-images.yml) 的 Git tag `vX.Y.Z` 触发；普通 `main` 合并不会凭空产生可下载的 release 镜像。若包仍为私有，需要相应 GHCR 读取权限。

从可信源码 checkout 的 SeeAlpha 根目录执行：

```bash
./seealpha container up vX.Y.Z         # 替换为已经存在的发布版本
./seealpha container admin create admin # 交互设置首次管理员口令
./seealpha container status
./seealpha container stop               # 仅停止进程，绝不删除卷
./seealpha container start              # 按固定镜像版本重新启动
```

配置位于 `.local/config/compose/local.env`（随机 Engine Token，文件权限 600）；Docker 命名卷分别持有 Platform SQLite/日志、Engine 状态/行情数据。只将 Web 的 3000 端口绑定到 `127.0.0.1`，Engine 与 API 的端口不发布给宿主机。Engine 需要访问公开行情源，因此容器网络允许出站；**不要在公网/局域网把此部署当作正式服务**，本模式设置 `COOKIE_SECURE=false`，只适合本机 HTTP 研究。

首次启动只对**空数据库**运行 Alembic；发现已有库时绝不在容器启动阶段迁移。更新 `vX.Y.Z` 标签、平台容器或 Engine 容器都不代表状态迁移获得授权，现有 `up` 会拒绝隐式变更已有标签；请先备份 SQLite、Engine 数据卷、令牌及未决订单证据，再另行执行受控升级。不要把这个 Compose 栈连接到正在交易的真实账户，也不要与现存 Engine 同时管理同一个账户。

图形安装器、多用户正式部署、自动备份与在线升级不属于当前预构建研究版。生产多租户部署仍遵循 [Tenant Docker 规范](TENANT_DOCKER_RUNTIME.md)。
