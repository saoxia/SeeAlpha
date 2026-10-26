# trend_following_long_5min · 5 分钟趋势跟随做多

[参数计算公式与来源](PARAMETER_SOURCES.md) · [缺乏校准依据的待讨论项](../PARAMETER_QUESTIONS.md)

本策略固定只做多，拥有独立的配置、指标、持仓、订单和回测入口，与另一方向不互相导入。
默认订单编号标签为 `TFL`；组合清单可按成员覆盖。停止时仅撤销本策略订单。
没有 `mode` 或 `--strategy-mode` 开关。标的是 Binance ETHUSDT USDT 本位永续。
默认读取已有 1 分钟 Catalog，内部聚合完整的 5 分钟及 1 小时 K 线。

## 默认交易规则

完整小时收盘价高于小时 MA20，单小时 MA20 斜率大于 6 小时前斜率，且 MA20 小时变化率严格大于 −0.1%。
完整 5 分钟 K 线确认 MA52 上穿 MA99，当根过滤通过才市价买入。
交叉仅当根有效，未通过过滤则跳过，持续位于均线同侧不重复开仓。

开仓成交后立即挂实际均价 × 0.995 的卖出止损市价单，绑定本笔持仓、仅减仓。
价格峰值浮盈达到 2% 后，止损线收紧为锁定峰值浮盈的 65%（回吐 35%），只向盈利方向移动。
采用 `peak_profit_exit=True`、`peak_profit_lock=0.65`、`dynamic_peak_profit_lock=False`。
分钟数据每根完整持有的分钟结束后更新峰值，跳过入场分钟极值；新保护线不回溯触发，
收盘已越线则按当前市价退出。跳价和手续费会影响实际收益。

默认最多一份持仓，每份使用 100% 账户权益计算名义数量。现有参数名称
`max_positions_per_side=1`、`equity_fraction_per_side=1.0` 仅作用于本策略。
允许多份时，`additional_entry_spacing=0.04` 要求新仓距其他持仓实际均价严格超过 4%，
各份订单、成本和止损独立。全平后须观察价格回到 MA20 ± 0.2σ 才能重新入场。
`cooldown_bars=0`、`stop_loss_cooldown_seconds=0` 默认关闭冷静期。

## 实验参数

保留原策略对应方向的实验逻辑和默认值：`entry_trigger="breakout"` 可选突破入场；
`peak_profit_exit=False` 可选 MA 退出，`ma_period=20`、`first_exit_std=0.2`、
`final_exit_std=0.3`，`long_full_exit_on_first=True` 控制首次触线全平。
MA 退出仍受 `take_profit_activation_return=0.02` 启用门槛约束。
`exit_on_reverse_cross`、`exit_on_ma99`、`exit_on_ma_slope_reversal`、
`tighten_exits_with_slope_risk`、`profit_relaxes_slope_risk` 默认关闭。
风险倍率只保留 `long` 方向配置；另一方向参数不接受。
完整参数见 [`config.py`](config.py)。

## 独立回测

在项目根目录运行：

```bash
.venv/bin/python -m strategy_python.trend_following_long_5min.backtest
.venv/bin/python -m strategy_python.trend_following_long_5min.backtest --start 2026-03-01 --end 2026-09-01
```

默认 UTC 区间为 `[2021-01-01, 2026-09-01)`，完整包含 2026 年 8 月 31 日。
初始资金 500 USDT、杠杆 5、预热 11 天，使用 HEDGING 以支持多份独立持仓。
默认只读已有行情。支持公共 `--interval`、`--check-data` 等选项。
结果写入 `strategy_python/_report/backtest/trend_following_long_5min/<run_id>/`。

组合注册名称为 `trend_following_long_5min`，参数不再填写 `mode`；目前使用 `risk.mode="none"`。
若需要共享账户，可在组合清单中分别登记两个独立策略；独立回测分别计算账户和结果。
旧报告保留在 `_report/analysis/`，其中旧双向入口脚本须迁移到新包后才能再次执行。
