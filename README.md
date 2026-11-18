# SeeAlpha

**让量化交易研究与管理更容易上手。**

SeeAlpha 是一个面向量化交易的研究与管理平台，将行情数据、策略、回测和运行管理整合到 Web 界面中。底层交易能力基于 [NautilusTrader](https://nautilustrader.io/)，由独立的 [SeeAlpha Engine](https://github.com/saoxia/seealpha-engine) 承担。

项目正在持续开发中，目前主要围绕加密资产展开。

## 快速开始

**适用环境：** macOS、Linux；Windows 通过 WSL2 Ubuntu 运行。安装前需要 [Git](https://git-scm.com/)、[uv](https://docs.astral.sh/uv/)、[Node.js](https://nodejs.org/) **22.13.0 或更高版本**及 npm。

在 macOS / Linux 终端执行：

```bash
git clone https://github.com/saoxia/SeeAlpha.git
cd SeeAlpha
./seealpha install
./seealpha start
```

安装时会准备本机所需组件，并在交互式首次安装中引导创建管理员。启动后，在浏览器打开：

**http://127.0.0.1:3000**

常用命令：

```bash
./seealpha status   # 查看服务状态
./seealpha doctor   # 检查环境和服务
./seealpha stop     # 停止本机服务
```

如果安装时未创建管理员，可运行 `./seealpha admin create admin`。

**Windows 用户：** 下载或克隆本仓库后，在 PowerShell 的项目根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\seealpha.ps1 install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\seealpha.ps1 start
```

Windows 安装器会检查并引导配置 WSL2 Ubuntu；Git、uv、Node.js/npm 等工具仍需在 Ubuntu 内安装。Windows 原生运行暂不支持。详细步骤见 [安装指南](docs/INSTALLATION.md)。

## 产品范围与当前实现

- **行情与数据：** 已接入 Binance USD-M 永续合约公开行情，支持历史数据准备与分钟级行情。
- **策略与回测：** 管理和下发策略包，运行研究与回测任务；提供[双均线策略示例](strategy/ema_crossover/README.md)。
- **Web 管理：** 管理用户、租户、策略授权与研究任务，查看任务状态和日志。
- **账户与交易：** 已有 Binance 账户只读查询及永续合约手动交易代码路径，但真实账户与真实订单尚未完成验收。
- **更多市场：** 期权的部分行情与适配器能力已实现；现货、股票和其他期货等仍需逐步接入与验证。

SeeAlpha 由两个部分组成：本仓库负责 **Platform（Web + API）**，独立的 [seealpha-engine](https://github.com/saoxia/seealpha-engine) 负责行情、回测、策略运行及交易执行。

> **使用提示：** 当前快捷安装面向本机研究和开发，不代表生产环境或实盘交易已通过验收。安装不会自动开启实盘交易。Windows WSL2 的完整安装流程仍待真实机器端到端验证。

## 进一步阅读

- [完整安装指南与故障排查](docs/INSTALLATION.md)
- [项目进度与待办](PROJECT_STATUS.md)
- [系统架构](docs/ARCHITECTURE.md)
- [Web 前端](web/README.md) · [API 服务](api/README.md)
- [策略包示例](strategy/ema_crossover/README.md)
- [更多技术文档](docs/README.md)
