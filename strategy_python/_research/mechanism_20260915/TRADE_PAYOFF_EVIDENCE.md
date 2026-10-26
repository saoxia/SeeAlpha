# 趋势策略成交结构

用实际成交现金流扣除费用，按持仓汇总。每笔收益再除以入场总名义金额，帮助区分仓位大小与退出表现；这不是保证金收益率。

| 资产/方向/区间 | 版本 | 胜率 | 平均盈利交易收益率 | 平均亏损交易收益率 | 单位名义盈亏比 | 去掉最好5%交易后的净收益 |
|---|---|---:|---:|---:|---:|---:|
| ETH/TFL/validation | fixed_ETH_TFL_original | 25.3% | 2.18% | -0.60% | 3.63 | -135.47 |
| ETH/TFL/validation | fixed_ETH_TFL_optimized | 19.6% | 3.11% | -0.60% | 5.19 | -171.07 |
| ETH/TFL/validation | ETH_TFL | 38.6% | 2.13% | -1.41% | 1.52 | -89.26 |
| ETH/TFL/review | fixed_ETH_TFL_original | 24.5% | 2.10% | -0.60% | 3.50 | -293.73 |
| ETH/TFL/review | fixed_ETH_TFL_optimized | 15.7% | 3.16% | -0.60% | 5.27 | -414.86 |
| ETH/TFL/review | ETH_TFL | 36.8% | 2.21% | -1.40% | 1.58 | -174.58 |
| ETH/TFS/validation | fixed_ETH_TFS_original | 25.7% | 2.73% | -0.60% | 4.55 | -0.48 |
| ETH/TFS/validation | fixed_ETH_TFS_optimized | 23.5% | 3.85% | -0.60% | 6.41 | 18.72 |
| ETH/TFS/validation | ETH_TFS | 48.3% | 2.78% | -2.62% | 1.06 | 7.15 |
| ETH/TFS/review | fixed_ETH_TFS_original | 29.6% | 1.96% | -0.60% | 3.27 | -9.99 |
| ETH/TFS/review | fixed_ETH_TFS_optimized | 21.7% | 3.44% | -0.60% | 5.73 | 1.96 |
| ETH/TFS/review | ETH_TFS | 41.5% | 3.18% | -2.32% | 1.37 | -9.33 |
| BTC/TFL/validation | fixed_BTC_TFL_original | 17.7% | 1.93% | -0.60% | 3.21 | -239.49 |
| BTC/TFL/validation | fixed_BTC_TFL_optimized | 13.9% | 2.78% | -0.60% | 4.64 | -242.82 |
| BTC/TFL/validation | BTC_TFL | 44.3% | 1.72% | -1.19% | 1.44 | -54.89 |
| BTC/TFL/review | fixed_BTC_TFL_original | 21.8% | 1.84% | -0.60% | 3.06 | -265.84 |
| BTC/TFL/review | fixed_BTC_TFL_optimized | 16.0% | 2.65% | -0.60% | 4.42 | -291.47 |
| BTC/TFL/review | BTC_TFL | 43.6% | 1.20% | -1.04% | 1.16 | -114.24 |
| BTC/TFS/validation | fixed_BTC_TFS_original | 30.8% | 2.04% | -0.60% | 3.39 | -5.88 |
| BTC/TFS/validation | fixed_BTC_TFS_optimized | 17.4% | 4.35% | -0.60% | 7.25 | -22.01 |
| BTC/TFS/validation | BTC_TFS | 40.0% | 2.88% | -2.39% | 1.20 | -0.95 |
| BTC/TFS/review | fixed_BTC_TFS_original | 19.3% | 1.62% | -0.60% | 2.70 | -73.13 |
| BTC/TFS/review | fixed_BTC_TFS_optimized | 10.9% | 3.81% | -0.60% | 6.36 | -98.70 |
| BTC/TFS/review | BTC_TFS | 48.9% | 1.83% | -1.63% | 1.13 | -7.61 |

[完整数据](trade_payoff_shape.csv)。去掉最好5%是描述性集中度压力检查，没有重新定仓，不是另一个可交易策略。少量大盈利贡献主要收益时，可以支持“小亏损换取大趋势”的经济解释，但不能据此证明0.5%、2%、3%或4%是唯一最优值。

若新止损覆盖更多恢复路径，却使平均亏损扩大、平均盈利缩小或盈亏比下降，就不能把恢复覆盖率的提高等同于策略优化。还应结合实际交易数、跨时期结果和参数邻域，而不能只看胜率。

CSV另列在观察到的胜率p和平均净盈利W保持不变时的盈亏平衡平均净亏损p×W/(1−p)。这能解释小止损如何约束亏损，但改变止损也会改变p和W，所以不能把该恒等式误用来反推唯一最优止损值。
