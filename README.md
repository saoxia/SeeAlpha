# SeeAlpha

<img src="web/app/icon.svg" alt="SeeAlpha 标识" width="56" />

**跨交易产品的量化策略，让不懂编程的人也能借助 AI 参与策略开发。**

SeeAlpha 是一个量化交易研究与管理平台，将行情数据、策略、回测和运行管理整合到 Web 界面中。底层基于 [NautilusTrader](https://nautilustrader.io/) 和独立的 [SeeAlpha Engine](https://github.com/saoxia/seealpha-engine)，目前主要围绕加密资产持续开发。

## 核心方向

- **跨金融衍生产品的策略能力：** 面向永续合约、期权等不同交易产品，逐步建立统一的策略研究、组合对冲、风险管理与交易执行能力，而不局限于单一市场或单一合约。
- **AI Harness：用自然语言开发自己的策略：** 即使没有编程经验，也可以从描述交易想法开始，让 AI 辅助研究和编写策略；产品目标是进一步串联回测验证、策略发布及上线后的持续稳定运行。

**当前进展：** 永续合约已具备部分数据与交易能力，期权已有部分行情和适配器；跨产品完整实盘链路尚未验收。AI Harness 当前支持 AI 研究任务、会话及成果导出，**尚未实现 AI 直接调用 Engine 完成回测、自动上线策略或经过验证的生产级稳定运行**。详见[当前实现](#产品范围与当前实现)与 [AI Harness 说明](docs/HARNESS_INTEGRATION.md)。

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
- **策略与回测：** 管理和下发策略包，运行研究与回测任务；提供 [四个 Demo 演示策略](strategy/README.md)。EMA 双均线和多信号组合可使用现有单品种回测链路；资金费率 Carry 与期权保护布林带已提供研究逻辑，完整跨产品执行/回测仍需 Engine 接线.
- **Web 管理：** 管理用户、租户、策略授权与研究任务，查看任务状态和日志。
- **账户与交易：** 已有 Binance 账户只读查询及永续合约手动交易代码路径，但真实账户与真实订单尚未完成验收。
- **更多市场：** 期权的部分行情与适配器能力已实现；现货、股票和其他期货等仍需逐步接入与验证。

SeeAlpha 由两个部分组成：本仓库负责 **Platform（Web + API）**，独立的 [seealpha-engine](https://github.com/saoxia/seealpha-engine) 负责行情、回测、策略运行及交易执行。

> **使用提示：** 当前快捷安装面向本机研究和开发，不代表生产环境或实盘交易已通过验收。安装不会自动开启实盘交易。Windows WSL2 的完整安装流程仍待真实机器端到端验证。

## 从哪里开始

安装并登录后，可以按自己的目标选择入口：

| 你想做什么 | 对应入口 |
| --- | --- |
| 查看行情和账户概览 | **LIVE**（账户数据需要先配置账户） |
| 查看 K 线和交易界面 | **TRADE**（真实下单尚未验收） |
| 准备和管理行情数据 | **DATA** |
| 通过自然语言开展 AI 策略研究 | **AI Harness**（实验性功能，需另行启用；自动回测与上线仍在开发） |
| 管理租户与运行环境 | 右上角用户菜单中的 **租户**；管理员还可进入 **管理后台** |

例如，你可以从 **DATA 准备数据**开始，参考[四个演示策略](strategy/README.md)了解策略包，再通过已配置的 Engine 开展研究与回测。相关能力的可用性取决于数据、服务及权限配置。

## 进一步阅读

- [完整安装指南与故障排查](docs/INSTALLATION.md)
- [项目进度与待办](PROJECT_STATUS.md)
- [系统架构](docs/ARCHITECTURE.md)
- [Web 前端](web/README.md) · [API 服务](api/README.md)
- [演示策略](strategy/README.md) · [AI Harness 说明](docs/HARNESS_INTEGRATION.md)
- [更多技术文档](docs/README.md)
