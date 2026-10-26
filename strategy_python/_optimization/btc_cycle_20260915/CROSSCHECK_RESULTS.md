# 第一轮：跨时间复核

所有候选均来自事先固定开发筛选规则。2024和2025以来分别独立500 USDT。后段行情此前已经查看，不称盲样本外；同区间风险预算及费用一致。这里不是最终8策略交付结论。

| 位置 | 方案 | 区间 | 净收益 | 回撤 | 胜率 | 盈亏比 | 成交数 | 活跃月数 | 额外每侧5bp后 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| MRL_original | main | validation | -23.52 | 15.83% | 94.9% | 0.036 | 138 | 8 | -54.55 |
| MRL_original | main | review | -159.98 | 41.99% | 95.3% | 0.016 | 232 | 17 | -211.22 |
| MRL_optimized | main | validation | -4.64 | 12.41% | 98.4% | 0.015 | 126 | 8 | -32.41 |
| MRL_optimized | main | review | -162.67 | 42.36% | 95.9% | 0.014 | 193 | 17 | -205.23 |
| TFL_original | main | validation | -8.83 | 7.23% | 44.3% | 1.199 | 149 | 12 | -41.64 |
| TFL_original | main | review | -31.89 | 9.74% | 43.6% | 1.177 | 282 | 20 | -106.24 |
| TFS_original | main | validation | 14.27 | 2.79% | 40.0% | 2.361 | 20 | 5 | 11.73 |
| TFS_original | main | review | 6.69 | 2.40% | 48.9% | 1.191 | 45 | 13 | -0.57 |
| PBS_original | main | validation | -36.67 | 10.41% | 22.7% | 2.021 | 22 | 8 | -46.58 |
| PBS_original | main | review | 7.16 | 16.96% | 35.4% | 1.900 | 48 | 17 | -17.30 |
| MRL_original | flat | validation | -28.70 | 18.21% | 95.4% | 0.028 | 152 | 7 | -56.93 |
| MRL_original | flat | review | 36.42 | 5.14% | 93.6% | 2.903 | 188 | 5 | 2.19 |
| MRL_optimized | flat | validation | -30.46 | 16.15% | 97.3% | 0.015 | 110 | 7 | -49.55 |
| MRL_optimized | flat | review | 31.25 | 5.16% | 93.0% | 3.356 | 158 | 5 | 1.23 |
| TFL_original | cost5 | validation | -9.88 | 7.60% | 43.9% | 1.210 | 148 | 12 | -42.17 |
| TFL_original | cost5 | review | -28.70 | 9.16% | 43.8% | 1.179 | 272 | 20 | -98.39 |
| TFS_original | lock80 | validation | 9.92 | 2.01% | 42.9% | 1.907 | 21 | 5 | 7.26 |
| TFS_original | lock80 | review | 8.40 | 2.49% | 48.9% | 1.228 | 45 | 13 | 1.14 |
| PBS_original | reward2 | validation | -45.50 | 9.82% | 22.7% | 1.620 | 22 | 8 | -55.19 |
| PBS_original | reward2 | review | 44.81 | 9.40% | 43.8% | 1.640 | 48 | 17 | 18.52 |
| PBS_original | falling_ma | validation | -39.85 | 12.30% | 8.3% | 4.107 | 12 | 7 | -44.48 |
| PBS_original | falling_ma | review | -5.52 | 12.46% | 26.1% | 2.674 | 23 | 14 | -18.03 |

## 逐项判定

| 位置/方案 | 开发期 | 2024同时改善 | 近期同时改善 |
|---|---|---|---|
| MRL_original/flat | development_pass | False | True |
| MRL_optimized/flat | development_pass | False | True |
| TFL_original/cost5 | development_pass | False | True |
| TFS_original/lock80 | development_pass | False | False |
| PBS_original/reward2 | development_pass | False | True |
| PBS_original/falling_ma | development_pass | False | False |

同时改善要求收益更高、回撤不高且交易数不少于10和按信号周期折算后基线25%的较大者；这是审慎的描述性门槛，不是统计显著性。成本、单位名义收益、活跃性等完整指标见[CSV](crosscheck_comparison.csv)。去掉最好五笔仅为集中度描述，不是可交易策略。未通过者返回假设诊断，不用风险缩放掩盖机制失败。

![独立区间资金曲线](crosscheck_curves.png)
