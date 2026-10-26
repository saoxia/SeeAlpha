# 第二轮：跨时间复核

所有候选均来自事先固定开发筛选规则。2024和2025以来分别独立500 USDT。后段行情此前已经查看，不称盲样本外；同区间风险预算及费用一致。这里不是最终8策略交付结论。

| 位置 | 方案 | 区间 | 净收益 | 回撤 | 胜率 | 盈亏比 | 成交数 | 活跃月数 | 额外每侧5bp后 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| MRL_original | next_base | validation | -23.52 | 15.83% | 94.9% | 0.036 | 138 | 8 | -54.55 |
| MRL_original | next_base | review | -159.98 | 41.99% | 95.3% | 0.016 | 232 | 17 | -211.22 |
| MRL_optimized | next_base | validation | -4.64 | 12.41% | 98.4% | 0.015 | 126 | 8 | -32.41 |
| MRL_optimized | next_base | review | -162.67 | 42.36% | 95.9% | 0.014 | 193 | 17 | -205.23 |
| TFL_original | next_base | validation | -8.83 | 7.23% | 44.3% | 1.199 | 149 | 12 | -41.64 |
| TFL_original | next_base | review | -31.89 | 9.74% | 43.6% | 1.177 | 282 | 20 | -106.24 |
| TFS_original | next_base | validation | 14.27 | 2.79% | 40.0% | 2.361 | 20 | 5 | 11.73 |
| TFS_original | next_base | review | 6.69 | 2.40% | 48.9% | 1.191 | 45 | 13 | -0.57 |
| PBS_original | next_base | validation | -36.67 | 10.41% | 22.7% | 2.021 | 22 | 8 | -46.58 |
| PBS_original | next_base | review | 7.16 | 16.96% | 35.4% | 1.900 | 48 | 17 | -17.30 |
| MRL_original | space_units2_same_risk | validation | 11.57 | 19.67% | 98.5% | 0.017 | 200 | 7 | -27.49 |
| MRL_original | space_units2_same_risk | review | -143.99 | 40.81% | 98.8% | 0.006 | 321 | 18 | -207.14 |
| MRL_original | space_units1_same_unit | validation | 1.39 | 4.74% | 98.0% | 0.022 | 98 | 8 | -6.18 |
| MRL_original | space_units1_same_unit | review | -25.54 | 9.80% | 98.6% | 0.009 | 212 | 17 | -43.78 |
| MRL_optimized | space_units2_same_risk | validation | 57.43 | 8.29% | 98.8% | 0.027 | 166 | 8 | 23.62 |
| MRL_optimized | space_units2_same_risk | review | -131.61 | 38.51% | 98.7% | 0.007 | 310 | 18 | -193.00 |
| MRL_optimized | space_units1_same_unit | validation | 4.08 | 4.66% | 97.8% | 0.026 | 93 | 8 | -3.13 |
| MRL_optimized | space_units1_same_unit | review | -25.82 | 10.04% | 98.4% | 0.010 | 188 | 17 | -41.60 |
| TFL_original | space_budget0.5 | validation | -10.46 | 4.31% | 42.6% | 1.179 | 141 | 12 | -25.02 |
| TFL_original | space_budget0.5 | review | -15.21 | 4.55% | 43.8% | 1.156 | 267 | 20 | -48.49 |
| TFS_original | next_slope_level | validation | 19.02 | 2.32% | 50.0% | 2.271 | 14 | 4 | 17.13 |
| TFS_original | next_slope_level | review | -1.64 | 2.23% | 46.9% | 1.080 | 32 | 11 | -6.28 |
| TFS_original | space_cap0.5 | validation | 14.27 | 2.79% | 40.0% | 2.361 | 20 | 5 | 11.73 |
| TFS_original | space_cap0.5 | review | 7.49 | 2.31% | 48.9% | 1.223 | 45 | 13 | 1.14 |
| PBS_original | space_risk0.005 | validation | -13.14 | 3.09% | 23.8% | 1.560 | 21 | 8 | -16.14 |
| PBS_original | space_risk0.005 | review | 10.86 | 4.29% | 38.3% | 1.983 | 47 | 17 | 3.55 |

## 逐项判定

| 位置/方案 | 开发期 | 2024同时改善 | 近期同时改善 |
|---|---|---|---|
| MRL_original/space_units2_same_risk | development_only | False | True |
| MRL_original/space_units1_same_unit | development_only | True | True |
| MRL_optimized/space_units2_same_risk | development_only | True | True |
| MRL_optimized/space_units1_same_unit | development_only | True | True |
| TFL_original/space_budget0.5 | development_only | False | True |
| TFS_original/next_slope_level | development_only | True | False |
| TFS_original/space_cap0.5 | development_only | False | True |
| PBS_original/space_risk0.005 | development_only | True | True |

同时改善要求收益更高、回撤不高且交易数不少于10和按信号周期折算后基线25%的较大者；这是审慎的描述性门槛，不是统计显著性。成本、单位名义收益、活跃性等完整指标见[CSV](next_crosscheck_comparison.csv)。去掉最好五笔仅为集中度描述，不是可交易策略。未通过者返回假设诊断，不用风险缩放掩盖机制失败。

![独立区间资金曲线](next_crosscheck_curves.png)
