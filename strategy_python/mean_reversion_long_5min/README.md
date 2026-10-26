# mean_reversion_long_5min 均值回归做多策略

[参数计算公式与来源](PARAMETER_SOURCES.md) · [缺乏校准依据的待讨论项](../PARAMETER_QUESTIONS.md)

从原 ETHUSDT v4 均值回归策略中独立拆出，仅做多。默认以 1 分钟数据驱动，内部聚合多周期特征，在布林带下轨挂首仓限价单，结合 MA99 过滤、风险定仓、四层加仓和分段止盈。

主类为 `MeanReversionLong5MinStrategy`，配置类为 `MeanReversionLong5MinConfig`。
交易方向由策略固定，不接受 `mode` 或 `--strategy-mode`，配置和运行状态不包含另一方向。

## 参数与职责

`max_units=4`：最大仓位单元数；`bollinger_period=20` / `bollinger_std_multiplier=2.0`：布林带参数；`risk_reference_days=120`：风险参考窗口；`hard_stop_distance=0.25`：硬止损距离；`cycle_risk_fraction=0.30`：周期基础风险比例。完整参数见 `config.py`。

硬止损后，6 小时 K 线最低价回到 MA52 上方才解除等待；保留多头专属日线 MA52 确认及 L-U 风险折减。

`config.py` 定义静态配置，`features.py` 聚合行情并维护指标，`signal.py` 产生信号，`position.py` 表达目标仓位，`risk.py` 管理定仓和恢复门槛，`execution.py` 处理订单与成交，`state.py` 保存运行状态，`strategy.py` 负责生命周期和模块编排。

## 回测

默认直接读取已有本地数据，缺失部分跳过；全部缺失则跳过本次回测。
只有显式添加 `--check-data` 才检查归档并补齐数据。`--force` 和
`--refresh-archives` 需要配合 `--check-data` 使用。

使用 `--interval 1s`、`--interval 1m` 或 `--interval 5m` 指定输入数据，策略的高周期
特征在内存中聚合。开启检查时只补缺失数据；只有显式 `--check-data --force` 才重建包含 135 天预热期的
TradeTick 和输入周期，其他周期不变。不同输入周期可能改变成交模拟结果。

在项目根目录执行，时间使用 UTC，区间左闭右开：

```bash
uv run python -m strategy_python.mean_reversion_long_5min.backtest \
  --start 2026-08-01 --end 2026-08-08
```

回测沿用 500 USDT 初始资金、5 倍杠杆和 HEDGING 持仓模式，额外加载 135 天预热数据。
行情统一复用根目录 `data/`；输出独立保存到`strategy_python/_report/backtest/mean_reversion_long_5min/<run_id>/`：

- `parameters.json`：含方向的策略名称、策略类、实际配置、市场和账户设置。
- `summary.json`：引擎汇总指标，最后生成，表示报表导出完成。
- `account.csv`、`fills.csv`、`positions.csv`：账户、成交和持仓明细。
- `report.html`：离线中文交互报告。

通用报告保留做多、做空和合计曲线；本策略只有做多成交。累计收益按完整持仓平仓时间计入已实现收益及结算币种手续费，不包含未平仓浮盈亏。回测风险评估仍需结合账户权益。

## 从原策略迁移

原 `mean_reversion_5min` 入口已拆为 `mean_reversion_long_5min` 和 `mean_reversion_short_5min`，不再提供双向模式。
本策略对应原 `long_only`，原 `long_hard_stop_distance` 与 `long_cycle_risk_fraction` 分别更名为 `hard_stop_distance` 与 `cycle_risk_fraction`。
拆分保留原方向交易规则；后续可在本目录独立优化。

需要与另一方向共用账户时，使用 [`portfolio_backtest.py`](../PORTFOLIO.md) 组合入口。单独运行本入口不启用共享预算。

回测入口通过公共 `StrategyBacktest` 注册名称、默认设置和策略工厂；公共参数无需在策略
中逐项转发。新增策略可参考 [`../README.md`](../README.md) 的“新策略如何复用回测入口”。
