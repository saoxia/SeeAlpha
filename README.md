# Nautilus 交易系统

本仓库是一个基于 `nautilus-trader 2.0.0rc7` 候选版 软件包搭建的项目内交易系统，
并不是上游 NautilusTrader 源代码仓库的副本。

## 本机开发（2026-10-26）

项目已迁移到 macOS Apple Silicon；完整性校验通过后进行了环境清理。
本机开发环境已安装并验收：Python 3.14.9、NautilusTrader 2.0.0rc7、Rust 1.99.1。
全量 1,345 项测试通过；未启动长期采集或后台服务。详见 [开发环境说明](DEV_ENVIRONMENT.md)。
v2 接口、数据格式和版本可用性限制见 [升级记录](handover/ENGINE_UPGRADE.md)。
恢复步骤、数据缺失范围及迁移记录见 [开发交接说明](HANDOVER.md)。
下文研究进度和测试数量为旧机器历史记录，不能视为本机运行状态。

本机 `data` 已链接到外置 APFS 卷 `ssd1`，具体位置和使用要求见 [数据存储说明](DATA_STORAGE.md)。

## Web 管理端

运行 `.venv/bin/python -m ops.web_console`，在浏览器打开
[综合管理端](http://127.0.0.1:8765)，即可查看采集与运维状态、运行项目检查、
发起 BTC/ETH 策略回测、查看任务日志及历史报告。使用现有本地数据及策略参数，
其他设备可访问 `http://运行机器的IP:8765`。无需安装新依赖。详见 [Web 管理端说明](ops/WEB_CONSOLE.md)。

## 当前进度与检查

[项目状态及待办](PROJECT_STATUS.md) · [BTC/ETH 采集健康](data/forward_validation/btc_eth_mrl_20260921/health.html) · [样本质量](data/forward_validation/btc_eth_mrl_20260921/quality.html) · [运维说明](ops/README.md)

BTC 现行四策略已完成历史诊断，目前没有版本通过对应的收益或稳定性门槛。旧机器记录了固定 90 天的 BTC/ETH 下轨限价前向观察；只采集公开行情和模拟报价，到期统一评估。

2026-09-22新增的[日线过滤与有限持仓周期研究](strategy_python/_optimization/mrl_regime_20260922/INTERPRETATION.md)已完成16组完整回放：持仓时间缩短，但主候选净收益恶化，三种新增规则均未通过门槛。研究代码和全部结果独立保存。

后续[入场质量优化](strategy_python/_optimization/mrl_entry_20260922/INTERPRETATION.md)已落地成本门槛与跌破收复确认，完成10组新增回放和8组参考核验。主候选降低回撤、减少费用压力下的亏损，但原费率全期净收益仍低于原策略，未通过替换门槛。该轮全项目检查1,186项测试通过。

最新[收复后持续走强研究](strategy_python/_optimization/mrl_recovery_20260922/INTERPRETATION.md)完成6组新增回放和10组参考核验。BTC原费率净亏损减至26.02 USDT，回撤降至6.63%；ETH仍亏损69.22 USDT，回撤15.44%。未形成跨市场一致改进，继续保留独立研究版本。最新全项目1,203项测试通过。

本机开发检查使用：

```bash
bash handover/check_dev.sh
```

该入口核验历史源码、运行 Ruff 和全量测试。旧观察数据未迁移，
`ops.check_project` 默认的前向冻结检查与 `ops.project_health` 暂不适用于本机；
开发模式使用 `ops.check_project --development`，管理端项目检查已使用该模式。

## 连续分钟行情层

新增独立的[统一行情层](market_data/README.md)，以已闭合 1 分钟 OHLCV 衔接本地历史、
REST 补齐与 WebSocket 实时数据，包含连续检查点、重启回放、来源冲突检测和数据就绪控制。
仅采集公开行情，尚未接入真实账户或自动启用现行策略；冻结前向观察保持独立。

```bash
.venv/bin/python -m market_data.service serve
.venv/bin/python -m market_data.service status
```

## 进入项目环境

```bash
source ./activate.sh
```

激活脚本会把 Python、虚拟环境、下载的依赖、缓存、工具和临时文件都保存在
本项目目录内。

## 常用命令

以下同步环境命令供安装或明确需要重建环境时使用；前向观察运行期间，改变依赖版本会触发冻结校验，日常检查优先使用上面的本地环境入口。

```bash
bash handover/bootstrap.sh
uv run python -c "import nautilus_trader; print(nautilus_trader.__version__)"
uv run pytest
uv run ruff check .
```

测试统一放在根目录的 `tests/`，按 `pipeline/`、`strategy_python/` 及其子模块分组，
文件名继续使用 `*_test.py`。测试按关键行为和出错影响组织，不要求每个 Python 文件
都有对应的测试文件。
相关的小用例集中维护，重点覆盖交易规则、风控、数据处理和完整回测流程；
具体约定见 [`strategy_python/README.md`](strategy_python/README.md#文件和测试命名)。

仅在需要时添加特定交易所的集成。密钥必须放在 `.env` 或其他已忽略的本地
文件中，禁止提交真实交易密钥。

## 策略

BTC 的现行研究主清单为趋势跟随多空、均值回归多空四个策略，统一入口和历史版本说明见
[BTC策略清单](strategy_python/BTC_STRATEGIES.md)。已合并的重复名称只指向同一份实现，
历史候选不计作额外的现行策略。

项目包含 ETHUSDT v4 均值回归做多、均值回归做空、趋势跟随做多和趋势跟随做空四个策略。每个策略都是独立包，
目录和依赖约束详见 [`strategy_python/README.md`](strategy_python/README.md)：

- [`strategy_python/mean_reversion_long_5min/`](strategy_python/mean_reversion_long_5min/)：
  ETHUSDT v4 均值回归做多策略，在布林带下轨挂首仓，保留四层风险定仓、分段止盈、
  MA99 过滤及多头 MA52 恢复规则。
- [`strategy_python/mean_reversion_short_5min/`](strategy_python/mean_reversion_short_5min/)：
  从原策略独立拆出的均值回归做空策略，在布林带上轨挂首仓，保留原空头风险定仓和
  6 小时 MA52 恢复门槛，便于单独优化。
- [`strategy_python/trend_following_long_5min/`](strategy_python/trend_following_long_5min/)：趋势跟随做多。
- [`strategy_python/trend_following_short_5min/`](strategy_python/trend_following_short_5min/)：趋势跟随做空。
  两者分别配置与回测，采用 MA52/MA99 交叉入场及峰值浮盈保护。

两个均值回归策略也可以通过 [`portfolio_backtest.py`](strategy_python/PORTFOLIO.md)
组合入口共用一个账户回测。默认总资金 500 USDT，总计划风险比例 30%，多空各占一半风险预算。

```bash
uv run python -m strategy_python.portfolio_backtest \
  --start 2026-08-01 --end 2026-08-08
```

可用 `--config strategy_python/_portfolio/mean_reversion.json` 选择运行清单。
清单定义启用的策略、各自参数、成员风险权重和账户设置，也支持同一策略的多个参数实例。
结果输出在 `strategy_python/_report/backtest/portfolio/<清单名称>/`，包含唯一账户、
各策略贡献统计及独立成交、持仓明细。

## Binance ETHUSDT 合约回测

每个策略目录中的 `backtest.py` 是该策略自己的回测入口。
默认读取已有 Catalog；显式添加 `--check-data` 才通过
`pipeline/prepare_data.py` 检查并准备缺失数据。行情数据统一保存在 `data/`。

在项目根目录分别运行趋势跟随做多、做空策略：

```bash
.venv/bin/python -m strategy_python.trend_following_long_5min.backtest
.venv/bin/python -m strategy_python.trend_following_short_5min.backtest
```

做多默认使用 UTC `[2021-01-01, 2026-09-01)`，做空默认使用
`[2026-03-01, 2026-09-01)` 区间，均完整包含 2026 年 8 月 31 日。
可用 `--start`、`--end` 指定区间。两个入口各自固定方向，无 `--strategy-mode` 开关。

运行均值回归做多策略：

```bash
uv run python -m strategy_python.mean_reversion_long_5min.backtest \
  --start 2026-08-01 --end 2026-08-08
```

运行独立的均值回归做空策略：

```bash
uv run python -m strategy_python.mean_reversion_short_5min.backtest \
  --start 2026-08-01 --end 2026-08-08
```

两个均值回归策略方向固定，不再接受 `--strategy-mode`；原 `mean_reversion_5min`
入口已移除。各自的 `hard_stop_distance` / `cycle_risk_fraction` 替代原带方向前缀的参数。
两者默认均额外加载开始时间之前 135 天的 1 分钟线，预热 120 天风险锚点、312 小时近期
窗口和各级均线，预热期不下单。回测沿用 `OmsType.HEDGING`、500 USDT 初始资金
和 5 倍杠杆；配置、订单标记和报告名称明确区分方向，结果分别写入各自策略目录。

迁移实现保留策略规则，但不会伪造 LEAN 的虚拟成交账户。限价、止损、滑点、
保证金和手续费均由 NautilusTrader venue/execution 模型执行，所以 LEAN 归档中的
PnL 只能用于迁移对照，不能直接当作本引擎的预期结果。上实盘前应先用同一批
1 分钟原始数据逐笔核对成交和退出原因。

默认仅读取已有本地数据；添加 `--check-data` 后才检查并下载、转换缺失归档，
之后复用本地校验通过的数据。通过 `--check-data --refresh-archives` 可以在回测前
重新检查 Binance 是否更新过历史归档。

程序会输出账户、成交和持仓三个基础报表，并自动保存到统一报告目录
`strategy_python/_report/backtest/<策略名称>/<run_id>/`，同时记录参数快照和引擎汇总指标。该回测使用 K 线级撮合，无法还原
K 线内部的价格路径、点差、盘口深度和真实排队情况。

所有单策略及组合回测均支持 `--interval 1s|1m|5m`，默认沿用原输入周期。
高周期信号从输入线按 UTC 边界在内存聚合，只消费完整闭合周期；3m、15m 等无法
组成当前策略 5m 信号的输入会被拒绝。更换输入周期可能改变成交模拟和止损检查结果。

所有单策略和组合回测默认读取已有 Catalog，缺失日期和 K 线跳过，不下载、不补齐、
不重建；整个区间都没有数据时提示并跳过。显式添加 `--check-data` 才使用 Pipeline
检查归档并补齐数据。`--force` 和 `--refresh-archives` 需同时启用 `--check-data`。
组合的 `--catalog-only` 保留兼容，等同于默认行为。原 `--no-check-data` 参数已移除。

例如（只读已有数据）：

```bash
.venv/bin/python -m strategy_python.mean_reversion_long_5min.backtest \
  --start 2026-08-01 --end 2026-08-02 --interval 1m
```

Pipeline 默认只补缺失，已有 Parquet 不因配置或版本变化而重建。
需要重建时显式添加 `--check-data --force`；回测中的强制范围包含预热日期，重建该范围的
TradeTick 和输入周期，其他周期保留。`--refresh-archives` 不隐含重建。
组合的 `--catalog-only` 不允许与 `--check-data`、`--force` 或
`--refresh-archives` 同时使用。

## 同步 Binance 公开归档

默认数据集配置为 Binance USD-M 永续 `BTC-USDT`、`ETH-USDT`、`SOL-USDT` 的逐笔 `trades`。
批量准备命令会从同一份 trades 生成 Nautilus `TradeTick` 以及 1s、1m、5m、15m Bar：

```bash
.venv/bin/python -m pipeline.prepare_data \
  --start 2026-08-01 \
  --end 2026-08-02
```

[`pipeline/sync_binance_file.py`](pipeline/sync_binance_file.py) 用于将
`data.binance.vision` 上指定的单个公开数据归档同步到项目。既可以传完整 URL，
也可以传以 `data/` 开头的站内路径：

```bash
.venv/bin/python -m pipeline.sync_binance_file \
  data/futures/um/daily/trades/ETHUSDT/ETHUSDT-trades-2026-08-01.zip
```

数据默认写入 `data/raw/binance/` 并保留远端目录结构。脚本会下载配套的
`.CHECKSUM`、执行 SHA-256 校验、使用临时文件原子替换，并在网络中断后尝试
断点续传。重复执行时，已经通过校验的文件会被跳过。

可通过 `--output-dir` 指定其他数据根目录；只有在远端确实没有校验文件且能
接受完整性风险时，才应使用 `--skip-checksum`。查看全部参数：

```bash
.venv/bin/python -m pipeline.sync_binance_file --help
```

批量同步、Nautilus 转换、统一 `prepare_data()` 入口和目录内所有文件的职责，
详见 [`pipeline/README.md`](pipeline/README.md)。

回测完成后打开结果目录中的 `report.html` 查看中文交互报告，无需联网。
报告包含常规统计、参数、账户/成交/持仓明细，以及做多、做空、合计三条累计净收益曲线。
曲线按完整持仓平仓时间计入含结算币种手续费的已实现收益；分批退出延后至全部平仓时计入，
未平仓持仓与浮盈亏不计入。支持悬停、图例开关、缩放、拖动、自选日期和“全部 / 近 6 个月”。
“近 6 个月”相对回测结束时间计算，筛选保留原始累计值，指标卡统计完整回测区间。

2026-10-27: [Native capability adoption and validation](handover/NATIVE_CAPABILITY_ADOPTION.md).
