# BTC-USDT现行四策略

自2026-09-21起，主清单固定为趋势跟随与均值回归，多空各一个。
注册与工厂在 [btc.py](btc.py)，统一回测入口在 [btc_backtest.py](btc_backtest.py)。

| 主入口名称 | 策略 | 保留的实现 | 当前证据 |
|---|---|---|---|
| `trend_following_long` | 趋势跟随多头 | `btc_cycle_optimized_20260915_tfl_original` | [修正后全测完成](_optimization/btc_long_validation_20260921/RESULTS.md)；原费率累计−3.06%，基础筛查未通过 |
| `trend_following_short` | 趋势跟随空头 | `btc_cycle_optimized_20260915_tfs_original` | [106项稳定域检验未通过](_optimization/tfs_stability_20260921/RESULTS.md)，保留冻结失败结果 |
| `mean_reversion_long` | 均值回归多头 | `btc_cycle_optimized_20260915_mrl_original` | 保留2σ基线；[修正后全测完成](_optimization/btc_long_validation_20260921/RESULTS.md)，原费率累计−5.95%，基础筛查未通过 |
| `mean_reversion_short` | 均值回归空头 | `btc_mean_reversion_short_5min` | 冻结迁移已全测；当前累计亏损60.02%，未通过有效性验证 |

例如：

```bash
.venv/bin/python -m strategy_python.btc_backtest trend_following_short \
  --start 2022-05-01 --end 2022-06-01
```

四个主策略现已在执行层检查合约快照的开仓数量和最低/最高名义金额；不放大仓位凑最低金额，退出不受开仓门槛阻断。
此前历史回测使用修正前执行代码；本轮修正后结果单独保存，不能把旧收益直接标作现代码绩效。

两个多头已完成原费率及每次成交额外5bp共4次完整回放，参数与风险预算保持不变。
趋势多头原费率累计−3.06%、最大回撤6.26%，成本压力后累计−7.14%；
均值回归多头原费率累计−5.95%、最大回撤14.26%，成本压力后累计−14.94%。
两者均未通过基础有效性筛查，保留失败结果并停止为这两个版本扩大参数搜索。
详见[两个多头图表报告](_optimization/btc_long_validation_20260921/report.html)。
至此，四个主策略均有执行修复后的完整历史结果；当前没有版本通过其对应的收益或稳定性验证。

随后完成了[BTC/ETH 入场信号与亏损归因](_optimization/btc_eth_signal_attribution_20260921/report.html)。
两市场均值回归多头都呈现高胜率、小额止盈与少数大额止损相抵的结构；BTC 699 笔盈利合计 +207.25 USDT，被 12 笔亏损合计 −237.00 USDT 抵消。
剥离账户限制、记录未成交候选并匹配随机对照后，均值回归多头和趋势空头均未达到预设的双市场信号证据门槛。
固定入场的 24 小时退出虽然改善部分历史结果，但未得到跨市场、近期与成本压力的一致支持，未据此修改策略。
当前继续保留冻结基线并停止扩大历史参数搜索；若继续研究，先用冻结之后新产生的数据验证下轨限价入场的净优势。
详见[文字结论](_optimization/btc_eth_signal_attribution_20260921/RESULTS.md)与[研究方案](_optimization/btc_eth_signal_attribution_20260921/PROTOCOL.md)。

已落实 [BTC/ETH 下轨限价入场的前向观察](../forward_validation/README.md)：冻结信号规则和成本口径，使用此后新产生的公开行情持续记录 90 天，到期统一评估。当前只运行行情观察与模拟报价，不使用账户资金。可查看[采集健康](../data/forward_validation/btc_eth_mrl_20260921/health.html)及[冻结方案](../forward_validation/PROTOCOL.md)。

2026-09-22按新的优化要求，另建[日线过滤与有限持仓周期研究](mean_reversion_long_regime_5min/README.md)，完成BTC/ETH、四组固定规则、两档费用共16次完整回放。三种新增规则均未通过门槛。组合主候选虽将最长持仓由约101/118天缩至24小时，原费率BTC净收益却从−29.74降至−50.40 USDT，ETH从−52.15降至−97.47 USDT；成本压力后回撤也高于基线。保留独立研究代码和失败结果，不替换四策略入口。详见[结果解释](_optimization/mrl_regime_20260922/INTERPRETATION.md)及[全部对照](_optimization/mrl_regime_20260922/RESULTS.md)。

后续新增[入场质量研究](mean_reversion_long_entry_5min/README.md)，实现成本门槛与跌破收复确认，完成10组新增回放及8组参考核验。主候选原费率BTC/ETH回撤降至9.45%／16.82%，净收益仍为−36.06／−68.19 USDT，低于原策略；费用压力下亏损减少但仍为负。该版本没有通过替换门槛，独立保留代码和[完整解释](_optimization/mrl_entry_20260922/INTERPRETATION.md)，不计入现行四策略。

最新[收复后持续走强候选](mean_reversion_long_recovery_5min/README.md)在后续完整一分钟收盘突破确认线高点时尝试限价入场。6组新增回放和10组参考核验完成，BTC原费率净收益−26.02 USDT、回撤6.63%，ETH−69.22、回撤15.44%；回撤降低但全期／近期收益均为负，平均单笔净亏损增加，仍未通过替换门槛。详见[结论与原因](_optimization/mrl_recovery_20260922/INTERPRETATION.md)，默认四策略与现有前向观察不变。

趋势空头本轮在BTC/ETH各做25格、原费率与每次成交额外5bp两档压力测试，另有中心与原版对照，合计106次。
BTC原费率全期25/25格盈利，但2025年以后只有7/25格盈利；额外5bp后近期为0/25。
ETH额外5bp后近期同样为0/25。按冻结协议停止扩大这个候选的参数搜索，不从已有图中换选最高点。
详见[图表报告](_optimization/tfs_stability_20260921/report.html)。这些是已见历史诊断，不是独立样本外验证。

参数与风险预算由各自保留版本决定；四个入口不是共享账户组合，也不表示已经通过实盘验证。
趋势多头、趋势空头、小时反弹空头的最终 original/optimized 两位置，经包名归一化后AST完全一致；
现各只保留一份实现，旧名为兼容导入。原始文件已校验归档，详见
[整理记录](_optimization/btc_consolidation_20260921/RESULTS.md)。
均值回归多头2σ/2.25σ存在真实差异，未误判为重复；选2σ作为默认基线不依据本轮收益择优。
小时反弹空头（PBS）属于独立的趋势反弹失败机制，移出四策略主清单；假跌破等也保留为研究记录。
其他历史迁移版、波动尺度版、机制版继续用于追溯，不作为额外的现行策略。

## 历史：固定参数八位置迁移

**BTC波动尺度适配版：** [8个适配策略、换算公式及入口](BTC_SCALE.md)。以下目录和历史结果是保留的固定参数对照版。

[完整回测结果](_optimization/btc_transfer_20260914/RESULTS.md) · [迁移协议](_optimization/btc_transfer_20260914/PROTOCOL.md)

保留对应ETH策略的信号和风险参数，使用BTC-USDT永续合约，未按BTC收益再次选参。8个目录各自包含完整领域模块和独立回测入口。

| 家族 | 版本 | BTC策略目录 | ETH来源 |
|---|---|---|---|
| 均值回归多头 | 原版 | [btc_mean_reversion_long_5min](btc_mean_reversion_long_5min/README.md) | [mean_reversion_long_5min](mean_reversion_long_5min/README.md) |
| 均值回归多头 | 优化版 | [btc_mean_reversion_long_optimized_5min](btc_mean_reversion_long_optimized_5min/README.md) | [mean_reversion_long_optimized_5min](mean_reversion_long_optimized_5min/README.md) |
| 趋势多头 | 原版 | [btc_trend_following_long_5min](btc_trend_following_long_5min/README.md) | [trend_following_long_5min](trend_following_long_5min/README.md) |
| 趋势多头 | 优化版 | [btc_trend_following_long_optimized_5min](btc_trend_following_long_optimized_5min/README.md) | [trend_following_long_optimized_5min](trend_following_long_optimized_5min/README.md) |
| 趋势空头 | 原版 | [btc_trend_following_short_5min](btc_trend_following_short_5min/README.md) | [trend_following_short_5min](trend_following_short_5min/README.md) |
| 趋势空头 | 优化版 | [btc_trend_following_short_optimized_5min](btc_trend_following_short_optimized_5min/README.md) | [trend_following_short_optimized_5min](trend_following_short_optimized_5min/README.md) |
| 小时反弹空头 | 原版 | [btc_pullback_short_1h](btc_pullback_short_1h/README.md) | [pullback_short_1h](pullback_short_1h/README.md) |
| 小时反弹空头 | 优化版 | [btc_pullback_short_optimized_1h](btc_pullback_short_optimized_1h/README.md) | [pullback_short_optimized_1h](pullback_short_optimized_1h/README.md) |

评估使用1分钟数据，初始500 USDT，账户保证金杠杆5倍。全区间为UTC `[2021-01-01,2026-08-31)`，近期为`[2025-01-01,2026-08-31)`；结束日不包含。数据实际截止8月30日。

PBS两版默认预算不同：原版0.5%风险/1倍名义上限，新版1.5%/3倍，报告另有同预算复核。其他三组使用相同预算。

单策略入口可运行各目录README中的命令；全区间批量运行采用90天分批加载，账户和策略状态连续，避免一次加载数百万根K线占用过多内存。

这些目录目前提供独立回测；本次没有运行共享账户BTC组合，不能把独立收益直接相加当作组合表现。
