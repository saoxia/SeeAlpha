# BTC 各版本回测统计

30个顶层版本逐项列出，另附21个内部代码目录覆盖情况、当前源码对应的全部原生回测、72项自建模拟。没有数据的目录不填零，不把分段结果冒充完整历史。

主表区间2021-01-01至2026-08-30（UTC），每版本初始500 USDT；收益为累计而非年化，手续费已扣。盈亏比为平均盈利/平均亏损绝对值，不是利润因子。各版本独立账户，不能简单相加当组合回测。

MRL=均值回归做多，TFL=趋势做多，TFS=趋势做空，PBS=回踩做空；original/optimized表示该批次所继承的位置。

## 初始BTC迁移版

| 版本 | 净收益USDT | 收益率 | 最大回撤 | 胜率 | 盈亏比 | 笔数 | 手续费USDT |
|---|---:|---:|---:|---:|---:|---:|---:|
| MRL_original | -166.30 | -33.26% | 44.70% | 99.03% | 0.0071 | 1034 | 53.94 |
| MRL_optimized | -151.56 | -30.31% | 49.88% | 99.19% | 0.0064 | 1109 | 67.26 |
| TFL_original | -241.41 | -48.28% | 56.32% | 21.07% | 3.2424 | 1049 | 377.72 |
| TFL_optimized | -220.64 | -44.13% | 57.74% | 15.17% | 4.9738 | 1002 | 391.11 |
| TFS_original | -45.98 | -9.20% | 12.46% | 23.49% | 2.7760 | 149 | 67.70 |
| TFS_optimized | -24.65 | -4.93% | 17.16% | 11.68% | 7.0097 | 137 | 63.50 |
| PBS_original | -42.77 | -8.55% | 10.75% | 35.29% | 1.3575 | 153 | 19.45 |
| PBS_optimized | -111.97 | -22.39% | 30.74% | 35.85% | 1.3842 | 159 | 57.30 |

## 波动尺度适配版

| 版本 | 净收益USDT | 收益率 | 最大回撤 | 胜率 | 盈亏比 | 笔数 | 手续费USDT |
|---|---:|---:|---:|---:|---:|---:|---:|
| MRL_original | -157.55 | -31.51% | 50.39% | 99.06% | 0.0077 | 1912 | 124.08 |
| MRL_optimized | -136.26 | -27.25% | 60.61% | 99.18% | 0.0070 | 1944 | 136.01 |
| TFL_original | -279.27 | -55.85% | 60.36% | 20.83% | 2.9866 | 1104 | 351.55 |
| TFL_optimized | -291.25 | -58.25% | 60.99% | 14.33% | 4.6806 | 1075 | 333.69 |
| TFS_original | -18.24 | -3.65% | 8.93% | 25.00% | 2.7820 | 152 | 71.35 |
| TFS_optimized | -75.33 | -15.07% | 20.40% | 12.33% | 5.1155 | 146 | 65.22 |
| PBS_original | -34.34 | -6.87% | 11.26% | 33.33% | 1.5442 | 129 | 18.74 |
| PBS_optimized | -85.25 | -17.05% | 30.62% | 33.33% | 1.6298 | 129 | 56.41 |

## 机制重构版

| 版本 | 净收益USDT | 收益率 | 最大回撤 | 胜率 | 盈亏比 | 笔数 | 手续费USDT |
|---|---:|---:|---:|---:|---:|---:|---:|
| MRL_original | -170.42 | -34.08% | 50.68% | 97.23% | 0.0202 | 1264 | 99.27 |
| MRL_optimized | -191.16 | -38.23% | 51.89% | 97.50% | 0.0167 | 1040 | 78.20 |
| TFL | -72.57 | -14.51% | 19.10% | 42.89% | 1.2494 | 949 | 206.93 |
| TFS | 22.31 | 4.46% | 3.84% | 46.02% | 1.3664 | 113 | 17.29 |
| PBS | -135.26 | -27.05% | 42.22% | 32.03% | 1.5834 | 153 | 62.87 |

## 最终循环优化版

| 版本 | 净收益USDT | 收益率 | 最大回撤 | 胜率 | 盈亏比 | 笔数 | 手续费USDT |
|---|---:|---:|---:|---:|---:|---:|---:|
| MRL_original | -47.17 | -9.43% | 14.71% | 98.18% | 0.0148 | 826 | 22.67 |
| MRL_optimized | -51.86 | -10.37% | 15.34% | 97.91% | 0.0167 | 716 | 19.38 |
| TFL_original | -12.00 | -2.40% | 6.30% | 42.13% | 1.2406 | 197 | 23.18 |
| TFL_optimized | -12.00 | -2.40% | 6.30% | 42.13% | 1.2406 | 197 | 23.18 |
| TFS_original | 32.53 | 6.51% | 2.21% | 51.22% | 1.3082 | 82 | 12.03 |
| TFS_optimized | 32.53 | 6.51% | 2.21% | 51.22% | 1.3082 | 82 | 12.03 |
| PBS_original | 3.67 | 0.73% | 10.91% | 37.33% | 1.7143 | 150 | 22.57 |
| PBS_optimized | 3.67 | 0.73% | 10.91% | 37.33% | 1.7143 | 150 | 22.57 |

## BTC假跌破收复研究版

| 版本 | 净收益USDT | 收益率 | 最大回撤 | 胜率 | 盈亏比 | 笔数 | 手续费USDT |
|---|---:|---:|---:|---:|---:|---:|---:|
| 48h_20%名义仓位 | 92.71 | 18.54% | 11.28% | 54.08% | 0.9583 | 588 | 52.61 |

## 比较口径

初始及尺度版PBS原/优化位的默认风险预算不同，不能把收益差异全部归因于信号；对应同预算对照保留在全部原生回测CSV。机制版表中采用main配置，不采用flat_risk或收益更好的实验替代。
机制版5个本体通过移除2024起始限制的历史副本回放；最终循环版TFL/TFS/PBS各两个位置共享参数，结果相同并非六份独立证据。
假跌破收复是自建分钟OHLC模拟，未作Nautilus原生验证；初始手续费模型为每边0.05%吃单。其余原生回测为挂单0.02%、吃单0.05%。不宜直接跨引擎按收益排名。资金费率、真实盘口与滑点未完整模拟；历史参与过选择，不能据此排除过拟合。

## 内部实验包逐项覆盖

| 目录 | 当前原生回测数 | 从2021开始的回测数 | 完整历史数 | 状态 |
|---|---:|---:|---:|---|
| strategy_python/_optimization/btc_cycle_20260915/next_packages/mrl_optimized | 25 | 15 | 4 | native_full_current |
| strategy_python/_optimization/btc_cycle_20260915/next_packages/mrl_original | 26 | 15 | 4 | native_full_current |
| strategy_python/_optimization/btc_cycle_20260915/next_packages/pbs_optimized | 0 | 0 | 0 | no_current_2021_native_record |
| strategy_python/_optimization/btc_cycle_20260915/next_packages/pbs_original | 29 | 14 | 3 | native_full_current |
| strategy_python/_optimization/btc_cycle_20260915/next_packages/tfl_optimized | 0 | 0 | 0 | no_current_2021_native_record |
| strategy_python/_optimization/btc_cycle_20260915/next_packages/tfl_original | 38 | 17 | 4 | native_full_current |
| strategy_python/_optimization/btc_cycle_20260915/next_packages/tfs_optimized | 0 | 0 | 0 | no_current_2021_native_record |
| strategy_python/_optimization/btc_cycle_20260915/next_packages/tfs_original | 23 | 12 | 3 | native_full_current |
| strategy_python/_optimization/btc_cycle_20260915/packages/mrl_optimized | 12 | 7 | 0 | native_2021_partial_only |
| strategy_python/_optimization/btc_cycle_20260915/packages/mrl_original | 14 | 7 | 0 | native_2021_partial_only |
| strategy_python/_optimization/btc_cycle_20260915/packages/pbs_optimized | 0 | 0 | 0 | no_current_2021_native_record |
| strategy_python/_optimization/btc_cycle_20260915/packages/pbs_original | 14 | 6 | 0 | native_2021_partial_only |
| strategy_python/_optimization/btc_cycle_20260915/packages/tfl_optimized | 0 | 0 | 0 | no_current_2021_native_record |
| strategy_python/_optimization/btc_cycle_20260915/packages/tfl_original | 15 | 9 | 0 | native_2021_partial_only |
| strategy_python/_optimization/btc_cycle_20260915/packages/tfs_optimized | 0 | 0 | 0 | no_current_2021_native_record |
| strategy_python/_optimization/btc_cycle_20260915/packages/tfs_original | 15 | 9 | 0 | native_2021_partial_only |
| strategy_python/_optimization/mechanism_full_20260915/packages/mechanism_mean_reversion_long_5min | 3 | 2 | 2 | native_full_current |
| strategy_python/_optimization/mechanism_full_20260915/packages/mechanism_mean_reversion_long_optimized_5min | 2 | 2 | 2 | native_full_current |
| strategy_python/_optimization/mechanism_full_20260915/packages/mechanism_pullback_short_1h | 1 | 1 | 1 | native_full_current |
| strategy_python/_optimization/mechanism_full_20260915/packages/mechanism_trend_following_long_5min | 1 | 1 | 1 | native_full_current |
| strategy_python/_optimization/mechanism_full_20260915/packages/mechanism_trend_following_short_5min | 1 | 1 | 1 | native_full_current |

## 全量数据文件

- [30个主版本指标与回测来源](main_versions.csv)
- [30个主版本逐年收益](main_annual.csv)
- [全部当前源码原生回测：356条，含各参数与分段](all_current_native_runs.csv)
- [自建模拟72项：含固定名义研究与实际账户，已分栏](custom_simulations.csv)
- [51个代码目录覆盖清单，含无数据目录](all_51_packages_coverage.csv)
- [150个参数方案覆盖清单](../btc_backtest_audit_20260916/variants.csv)
