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

先连接外置盘，确认卷名仍为 `ssd1`，在项目根目录执行：

```bash
source ./activate.sh
python --version
bash handover/check_dev.sh --quick
bash handover/check_dev.sh
python -m ops.web_console --host 127.0.0.1
```

最后一条手动启动管理端，浏览器打开 `http://127.0.0.1:8765`；Ctrl+C 停止。
验收过程中管理端已退出，没有留下长期服务。

激活支持 zsh 和 Bash。缺失数据目录或外置卷未挂载时，激活会明确失败，
不创建本机数据替代目录。其他机器可自行建立可用的本地 `data` 目录。
也可单独运行 `python -m ops.storage` 检查数据位置。

## 安装与重建

Python 环境统一安装入口：

```bash
bash handover/bootstrap.sh
```

脚本激活项目路径；uv 不存在时安装项目内 uv 0.13.5，随后安装 Python 3.14.9，
执行 `uv sync --locked` 并检查包兼容性。
`.python-version` 固定 Python；`pyproject.toml` 中默认启用 `dev` 和 `research` 分组，
普通 `uv sync` / `uv run` 会包含研究依赖。

本次按用户授权升级引擎及依赖，安装 31 个 Python 包；版本与两项发布可用性限制见 [v2 升级记录](handover/ENGINE_UPGRADE.md)。
日常依赖以根目录 `pyproject.toml` 和 `uv.lock` 为准。旧环境、旧锁文件和迁移测试缓存已清理。
新增依赖时明确更新声明和锁文件，再运行检查；不要无差别升级。

Rust 工具链安装、构建和单元测试入口（macOS ARM64）：

```bash
bash handover/build_rust.sh
```

`rust-toolchain.toml` 固定 Rust 1.99.1；构建使用 `Cargo.lock`。
激活脚本自动配置转换器路径。需要验证 Python 回退时：

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
| Rust 编译缓存和二进制 | `/Volumes/ssd1/NautilusTrader/.build-cache/rust-target` |
| 常规策略回测报告 | `strategy_python/_report/backtest/`，仍在本机 |

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
及显式开发检查模式 `ops.check_project --development`。
未指定该参数的原检查仍要求前向观察冻结数据；未迁移的数据不会被视为验证通过。

精确版本、测试报告路径和样本目录见 [本机验收记录](handover/local_environment.json)。
`handover/files.sha256.json` 是初次导入快照，
正常开发后不再代表当前全部文件。当前源码与测试验收使用 `check_dev.sh`。
