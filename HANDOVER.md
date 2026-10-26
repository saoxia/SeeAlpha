# NautilusTrader 本机开发交接

迁移日期：2026-10-26（北京时间）。目标环境：macOS / Apple Silicon。
项目直接位于 `/Users/lingyu/Documents/ChatGPT/SeeAlpha`，沿用当前目录的 Git 仓库。

## 已完成

- 从 `NautilusTrader-dev-handover-20261026.zip` 导入全部 8,680 个文件，未额外嵌套项目目录。
- 清理前按原清单校验 8,679 个文件，失败 0 个。
- 保留策略、参数、测试、行情管道、Web 管理端、Rust/C 源码及历史研究报告和冻结归档。
- 移除旧机器专用的后台服务、定时任务、宿主机脚本、安装器及系统状态快照。
- 激活脚本兼容本机默认 zsh 和 Bash，依赖锁文件及源环境版本列表保留。
- 前向观察的源码清单不再包含已删除的旧机器服务文件；本机新观察需建立新冻结记录。

历史研究快照中的旧路径作为研究出处保留，不批量修改。
原始压缩包仍在 Downloads，可用于恢复原始文件。
源机器环境与旧验收文件已清理；当前验收见 `handover/local_environment.json`。

## 本机环境已准备完成

2026-10-26 已安装项目内 Python 3.14.9、uv 0.13.5、Rust/Cargo 1.99.1，
NautilusTrader 已升级到 2.0.0rc7；其余依赖与适配说明见 [v2 升级记录](handover/ENGINE_UPGRADE.md)。
系统 Python 3.9.6 未改动。全量 1,345 项测试通过，Rust 单元测试 3 项通过。
小样本转换、真实回测引擎和管理端启动验收通过，未下载真实行情或启动长期采集。

```bash
bash handover/bootstrap.sh    # 按统一锁文件安装或重建 Python 环境
bash handover/build_rust.sh   # 安装固定 Rust 工具链并构建、测试转换器
source ./activate.sh
bash handover/check_dev.sh    # 开发源码、Ruff 和全量测试
```

研究依赖统一纳入 pyproject.toml 和 uv.lock。
数据和大型 Rust 编译产物放在外置盘；使用前连接卷 `ssd1`。
详细命令和验收边界见 [DEV_ENVIRONMENT.md](DEV_ENVIRONMENT.md)，
精确版本及测试路径见 [handover/local_environment.json](handover/local_environment.json)。

## 数据及研究状态

压缩包不含行情归档、Catalog、分钟行情数据库、前向观察数据库、原始证据、备份、
研究 Parquet/NumPy 中间缓存或访问口令，因此不能直接重现旧回测或续接旧观察。
CSV/HTML/XLSX 等保留文件属于研究结果，不是行情数据。
旧观察的起止日期与运行记录只作为历史背景；本机未注册自动采集或评估任务。

开发入口见根 README、`PROJECT_STATUS.md`、`strategy_python/BTC_STRATEGIES.md`、
`market_data/README.md`、`pipeline/README.md` 及 `tests/README.md`。

## 完整性检查

使用 `bash handover/check_dev.sh` 核验当前源码和测试。
初次导入记录 `handover/migration.json`、`handover/files.sha256.json` 仅作历史出处。
环境清理记录见 `handover/cleanup.json`；旧快照校验脚本已移除。
