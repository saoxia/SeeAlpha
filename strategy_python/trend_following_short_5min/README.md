# trend_following_short_5min · 5 分钟趋势跟随做空

[参数计算公式与来源](PARAMETER_SOURCES.md) · [缺乏校准依据的待讨论项](../PARAMETER_QUESTIONS.md)

本策略固定只做空，拥有独立的配置、指标、持仓、订单和回测入口，与另一方向不互相导入。
默认订单编号标签为 `TFS`；组合清单可按成员覆盖。停止时仅撤销本策略订单。
没有 `mode` 或 `--strategy-mode` 开关。标的是 Binance ETHUSDT USDT 本位永续。
默认读取已有 1 分钟 Catalog，内部聚合完整的 5 分钟及 1 小时 K 线。

## 默认交易规则

默认采用同时取消第 2、3、4、7 项的版本：不要求小时价格低于 MA20、小时 MA20 斜率下降、
小时 MA20 涨幅小于 +0.1%，也不要求平仓后回到 MA20 附近。
完整 5 分钟 K 线确认 MA52 下穿 MA99，当根过滤通过才市价卖出。
交叉仅当根有效，未通过过滤则跳过，持续位于均线同侧不重复开仓。

始终启用日线建仓过滤，每次建仓须同时满足：
UTC 当天正在形成的日线最低价严格低于当天动态 BOLL 下轨，
且前一根已收盘日线最低价严格低于该日收盘时的 BOLL 下轨。
默认 `daily_boll_entry_filter=True`、`daily_boll_period=20`、`daily_boll_std=2.0`。
默认 `daily_boll_max_width_ratio=None`、`daily_boll_upper_max_width_ratio=None`，
不按带宽或价格位置豁免日线低点过滤，也不另加上轨带宽限制。
BOLL 使用日收盘价 SMA20 ± 2 倍总体标准差；当天使用截至信号时刻的最新价和最低价，
不引用未来日收盘，不锁存盘中曾经满足的过滤信号。日线历史不足或分钟数据缺失时禁止新建仓，
遇到不完整日线会重新预热连续日线窗口。过滤仅影响建仓，已有持仓仍按原规则退出。
可用 `daily_boll_entry_filter=False` 关闭日线过滤。

开仓成交后立即挂实际均价 × 1.005 的买入止损市价单，绑定本笔持仓、仅减仓。
价格峰值浮盈达到 2% 后，止损线收紧为锁定峰值浮盈的 65%（回吐 35%），只向盈利方向移动。
采用 `peak_profit_exit=True`、`peak_profit_lock=0.65`、`dynamic_peak_profit_lock=False`。
分钟数据每根完整持有的分钟结束后更新峰值，跳过入场分钟极值；新保护线不回溯触发，
收盘已越线则按当前市价退出。跳价和手续费会影响实际收益。

默认最多一份持仓，每份使用 100% 账户权益计算名义数量。现有参数名称
`max_positions_per_side=1`、`equity_fraction_per_side=1.0` 仅作用于本策略。
`equity_fraction_per_side` 支持有限正倍数，例如 `3.0` 表示每份建仓名义金额为
账户权益（余额加合约浮动盈亏）的 3 倍；该参数不改变账户杠杆设置。
允许多份时，`additional_entry_spacing=0.04` 要求新仓距其他持仓实际均价严格超过 4%，
各份订单、成本和止损独立。全平后无需等待价格回到 MA20 ± 0.2σ，
但下一次建仓仍须出现新的 MA52/MA99 死叉并通过两条日线过滤。
`cooldown_bars=0`、`stop_loss_cooldown_seconds=0` 默认关闭冷静期。

## 实验参数

当前默认 `require_entry_cross=True`（1：5 分钟死叉）、
`require_hourly_price_filter=False`（2：小时价格低于 MA20）、
`require_hourly_slope_filter=False`（3：小时 MA20 斜率下降）、
`max_adverse_hourly_ma_return=None`（4：小时 MA20 涨幅限制）、
`require_reentry_zone=False`（7：平仓后回到 MA20 附近）。将第 2、3、7 项设为 `True`，
第 4 项设为 `0.001` 可恢复此前全部保留的基准。取消第 1 条时，每根完整 5 分钟线
检查其余条件并市价建仓；关闭第 3 条不会改用另一种斜率算法。

`daily_boll_only_entry=True` 为仅保留两条日线低点条件的对照模式：每根完整 5 分钟线
收盘时，空仓且两条日线条件通过即市价做空，不要求 MA52/MA99 死叉、小时趋势或
平仓后回到 MA20 附近，也不等待固定或止损冷静期。数据就绪要求和仓位计算保持不变。
此模式限定单份持仓、始终启用日线过滤和现有峰值止盈，原生止损及退出逻辑不变。
默认 `daily_boll_only_entry=False`，标准策略仍要求死叉触发。

可将 `daily_boll_max_width_ratio=0.22` 设为旧条件启用模式：仅当日线
`(上轨 - 下轨) / MA20 < 0.22` 且 `下轨 ≤ 最新价 ≤ MA20` 时检查两日低点。
可另设 `daily_boll_upper_max_width_ratio=0.22`，在最新价 ≥ 上轨且带宽比 ≥ 22% 时禁止建仓。
两项实验限制默认均关闭。

保留原策略对应方向的实验逻辑和默认值：`entry_trigger="breakout"` 可选突破入场；
`peak_profit_exit=False` 可选 MA 退出，`ma_period=20`、`first_exit_std=0.2`、
`final_exit_std=0.3`，`short_full_exit_on_first=True` 控制首次触线全平。
MA 退出仍受 `take_profit_activation_return=0.02` 启用门槛约束。
`exit_on_reverse_cross`、`exit_on_ma99`、`exit_on_ma_slope_reversal`、
`tighten_exits_with_slope_risk`、`profit_relaxes_slope_risk` 默认关闭。
风险倍率只保留 `short` 方向配置；另一方向参数不接受。
完整参数见 [`config.py`](config.py)。

## 独立回测

在项目根目录运行：

```bash
.venv/bin/python -m strategy_python.trend_following_short_5min.backtest
.venv/bin/python -m strategy_python.trend_following_short_5min.backtest --start 2026-03-01 --end 2026-09-01
```

默认 UTC 区间为 `[2026-03-01, 2026-09-01)`，完整包含 2026 年 8 月 31 日。
初始资金 500 USDT、杠杆 5、预热 21 天，使用 HEDGING 以支持多份独立持仓。
默认只读已有行情。支持公共 `--interval`、`--check-data` 等选项。
结果写入 `strategy_python/_report/backtest/trend_following_short_5min/<run_id>/`。

组合注册名称为 `trend_following_short_5min`，参数不再填写 `mode`；目前使用 `risk.mode="none"`。
若需要共享账户，可在组合清单中分别登记两个独立策略；独立回测分别计算账户和结果。
旧报告保留在 `_report/analysis/`，其中旧双向入口脚本须迁移到新包后才能再次执行。
