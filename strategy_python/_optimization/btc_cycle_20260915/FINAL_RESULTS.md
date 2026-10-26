# BTC 八个新版本：2021 年起连续回测

区间为 2021-01-01 至 2026-08-30（UTC）。各策略独立以 500 USDT 起步，账户与持仓跨年连续，手续费已计入。
八个独立新包已逐一完成全区间原生回测，并与所选研究版本逐笔成交、每日权益、配置及关键指标核对一致。旧文件哈希复核通过。

| 策略位置 | 版本 | 净收益 USDT | 收益率 | 最大回撤 | 胜率 | 盈亏比 | 交易数 |
|---|---|---:|---:|---:|---:|---:|---:|
| MRL_original | 优化前 | -170.42 | -34.08% | 50.68% | 97.23% | 0.0202 | 1264 |
| MRL_original | 优化后 | -47.17 | -9.43% | 14.71% | 98.18% | 0.0148 | 826 |
| MRL_optimized | 优化前 | -191.16 | -38.23% | 51.89% | 97.50% | 0.0167 | 1040 |
| MRL_optimized | 优化后 | -51.86 | -10.37% | 15.34% | 97.91% | 0.0167 | 716 |
| TFL_original | 优化前 | -72.57 | -14.51% | 19.10% | 42.89% | 1.2494 | 949 |
| TFL_original | 优化后 | -12.00 | -2.40% | 6.30% | 42.13% | 1.2406 | 197 |
| TFL_optimized | 优化前 | -72.57 | -14.51% | 19.10% | 42.89% | 1.2494 | 949 |
| TFL_optimized | 优化后 | -12.00 | -2.40% | 6.30% | 42.13% | 1.2406 | 197 |
| TFS_original | 优化前 | 22.31 | 4.46% | 3.84% | 46.02% | 1.3664 | 113 |
| TFS_original | 优化后 | 32.53 | 6.51% | 2.21% | 51.22% | 1.3082 | 82 |
| TFS_optimized | 优化前 | 22.31 | 4.46% | 3.84% | 46.02% | 1.3664 | 113 |
| TFS_optimized | 优化后 | 32.53 | 6.51% | 2.21% | 51.22% | 1.3082 | 82 |
| PBS_original | 优化前 | -135.26 | -27.05% | 42.22% | 32.03% | 1.5834 | 153 |
| PBS_original | 优化后 | 3.67 | 0.73% | 10.91% | 37.33% | 1.7143 | 150 |
| PBS_optimized | 优化前 | -135.26 | -27.05% | 42.22% | 32.03% | 1.5834 | 153 |
| PBS_optimized | 优化后 | 3.67 | 0.73% | 10.91% | 37.33% | 1.7143 | 150 |

## 选择理由与参数变化

- **MRL_original**：选择单层及0.075总风险预算，完整历史回撤约15%，较两层同预算的39%—44%显著低，54活跃月；接受期末收益低于两层同预算的取舍。属于风险调整，仍亏损，不能宣称正期望。 [新版本](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_mrl_original/README.md) · [参数变化](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_mrl_original/profile.json)
- **MRL_optimized**：选择单层及0.075总风险预算，完整历史回撤约15%，较两层同预算的39%—44%显著低，54活跃月；接受期末收益低于两层同预算的取舍。属于风险调整，仍亏损，不能宣称正期望。 [新版本](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_mrl_optimized/README.md) · [参数变化](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_mrl_optimized/profile.json)
- **TFL_original**：40分钟信号与半预算在完整历史收益和回撤均优于30分钟及费用过滤组合，68活跃月；20/30/40邻域已保留。仍亏损，且信号分布变化与历史选择偏差尚存。 [新版本](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_tfl_original/README.md) · [参数变化](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_tfl_original/profile.json)
- **TFL_optimized**：40分钟信号与半预算在完整历史收益和回撤均优于30分钟及费用过滤组合，68活跃月；20/30/40邻域已保留。仍亏损，且信号分布变化与历史选择偏差尚存。 [新版本](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_tfl_optimized/README.md) · [参数变化](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_tfl_optimized/profile.json)
- **TFS_original**：方向过滤加锁利的完整历史收益6.51%、回撤2.21%，优于单独持仓上限调整；额外5bp后仍盈利，去最佳五笔仍为正。仅82笔，2026亏损且近期收益不优于原基线，不能称各年皆优。 [新版本](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_tfs_original/README.md) · [参数变化](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_tfs_original/profile.json)
- **TFS_optimized**：方向过滤加锁利的完整历史收益6.51%、回撤2.21%，优于单独持仓上限调整；额外5bp后仍盈利，去最佳五笔仍为正。仅82笔，2026亏损且近期收益不优于原基线，不能称各年皆优。 [新版本](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_tfs_optimized/README.md) · [参数变化](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_tfs_optimized/profile.json)
- **PBS_original**：低风险0.005及2.5R目标的完整历史收益与回撤略优于2R，保留1.5/2/2.5分期邻域。额外2bp后转亏，去最佳五笔也转亏，属于脆弱的历史改善，不能称稳定盈利。 [新版本](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_pbs_original/README.md) · [参数变化](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_pbs_original/profile.json)
- **PBS_optimized**：低风险0.005及2.5R目标的完整历史收益与回撤略优于2R，保留1.5/2/2.5分期邻域。额外2bp后转亏，去最佳五笔也转亏，属于脆弱的历史改善，不能称稳定盈利。 [新版本](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_pbs_optimized/README.md) · [参数变化](/home/lly/NautilusTrader/strategy_python/btc_cycle_optimized_20260915_pbs_optimized/profile.json)

## 解释边界

[总、子、时、空研究过程与各轮完整证据](CYCLE_RESEARCH.md)

风险预算下降产生的少亏不能解释为入场逻辑已获得正期望。表中保留所有亏损、交易活跃性及成本敏感性；八个位置中的部分基线相同，不能视为八份独立统计证据。
2021—2023 包含机制系数训练数据，后续历史也已经参与多轮比较与选择。本结果不能排除过拟合，也不是盲样本验证。原先通过反复试验选出的数值，事后解释不能替代统计来源证据。
最大回撤使用分钟盯市，图为日末权益。年度收益来自连续账户，并非逐年重置本金。未计资金费率、真实滑点与逐日历史合约规则；额外 2/5bp 成本使用固定成交路径扣费，没有重新计算持仓。原始分钟数据有 938 分钟缺口，未伪造补价。

[完整指标、活跃性、费用及额外成本](final_comparison.csv) · [逐年收益](final_annual.csv) · [18 项候选全记录](FULL_CYCLE_RESULTS.md) · [八包验证记录](FINAL_VERIFICATION.json)

![优化前后连续 PnL 曲线](final_curves.png)
