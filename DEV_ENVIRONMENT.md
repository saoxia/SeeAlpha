# macOS 开发环境

验收日期：2026-10-26。运行平台为 Apple Silicon，采用项目内 uv/Python/Rust 环境。
系统 Python 保持 3.9.6；不修改系统包、全局 shell 配置或注册后台采集任务。

| 工具 | 已验证版本 |
|---|---|
| Python | 3.14.9 |
| uv | 0.13.5 |
| NautilusTrader | 2.0.0rc7 |
| pytest / Ruff | 9.1.1 / 0.17.2 |
| Rust / Cargo | 1.99.1 |
| Apple Clang | 21.0.0（现有 Xcode） |

## 日常使用

在项目根目录执行：

```bash
source ./activate.sh
python --version
python -m ruff check .
python -m pytest -q
python -m web.server --host 127.0.0.1
```

最后一条手动启动管理端，浏览器打开 `http://127.0.0.1:8765`；Ctrl+C 停止。
验收过程中管理端已退出，没有留下长期服务。

激活支持 zsh 和 Bash，不依赖数据目录或外置盘。数据位置由本机文件系统链接管理；
程序仅使用逻辑路径 `data/`，不检查挂载状态或推导外置盘位置。

## 安装与重建

Python 环境统一安装入口：

```bash
uv sync --locked
```

先安装 uv 并进入项目环境；可执行 `uv python install` 安装 `.python-version` 指定的 Python，
随后执行 `uv sync --locked` 和 `uv pip check`。本机现有 uv 位于 `.local/bin/`。
新机器先按 [uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/) 准备工具，
项目不再提供自动下载安装器。
`.python-version` 固定 Python；`pyproject.toml` 中默认启用 `dev` 和 `research` 分组，
普通 `uv sync` / `uv run` 会包含研究依赖。

本次按用户授权升级引擎及依赖，安装 31 个 Python 包；版本与两项发布可用性限制见 [v2 升级记录](docs/ENGINE_UPGRADE.md)。
日常依赖以根目录 `pyproject.toml` 和 `uv.lock` 为准。旧环境、旧锁文件和迁移测试缓存已清理。
新增依赖时明确更新声明和锁文件，再运行检查；不要无差别升级。

Rust 工具链安装、构建和单元测试入口（macOS ARM64）：

```bash
cargo build --release --locked --manifest-path pipeline/rust_converter/Cargo.toml
cargo test --release --locked --manifest-path pipeline/rust_converter/Cargo.toml
```

先安装 rustup（本机位于 `.local/cargo/bin/`）。Cargo 根据 `rust-toolchain.toml`
选择工具链，根据 `Cargo.lock` 构建；不再通过项目脚本重复声明版本。
转换器默认从本机 `pipeline/rust_converter/target/release/` 发现，不依赖激活脚本或数据目录。需要验证 Python 回退时：

```bash
NAUTILUS_RUST_CONVERTER=off python -m pytest tests/pipeline -q
```

现有 Xcode Clang 已通过动态库编译与 Python ctypes 加载检查。
历史研究 C 扩展保留原样，运行具体研究时再检查其编译参数和平台兼容性。

## 存储位置

| 内容 | 位置 |
|---|---|
| Python 依赖 | `.venv/` |
| uv / Python / Rust 工具链和下载缓存 | `.local/` |
| 行情、Catalog、状态及数据库 | `data` → `/Volumes/ssd1/NautilusTrader/data` |
| Rust 编译缓存和二进制 | `pipeline/rust_converter/target/`，位于本机 |
| 常规策略回测报告 | `data/reports/backtest/`，通过 data 链接存储 |

数据盘配置详见 [DATA_STORAGE.md](DATA_STORAGE.md)。
不要在任务运行时拔盘；更改卷名后需更新 `data` 链接。

## 验收记录

- 历史来源 69 个均通过原始归档/授权更新哈希核验；旧样式豁免已清零。
- Ruff 通过；pytest **1,345 项通过、无跳过**，54 条 pandas 解析参数弃用提示。
- Rust 单元测试 3 项通过；生成二进制为原生 ARM64。
- 10 条自造交易转换为 10 根分钟线，Python / Rust 结果一致。
- 从样本 Catalog 经真实回测引擎导出完整报告；样本没有交易，仅用于环境链路验收。
- 真实管理端启动后 `/api/state` 返回 HTTP 200，随后停止。
- 默认环境已按升级后的统一锁文件重建，uv pip check 通过。

本次适配还包括 macOS 进程组退出时的短暂权限状态、管理端外置数据根目录访问、
及 Web 管理端统一运行代码规范和测试。
前向观察功能已移除，项目检查不依赖其运行数据。

当前依赖版本以 `uv.lock`、`rust-toolchain.toml` 和转换器 `Cargo.lock` 为准。
开发检查使用 `python -m ruff check .` 与 `python -m pytest -q`。
Web 发起的检查保存报告到 `data/project_checks/latest.json`；直接运行标准命令时结果显示在终端。
当前策略源码基线位于 `tests/baselines/current_source_baselines.json`。
