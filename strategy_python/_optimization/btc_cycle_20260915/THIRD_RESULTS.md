# 第三轮组合与预算邻域

这是完整24项对照（22项新增回放加2项已完成MRL开发结果），并非最终策略接受结论。全部历史已看过，2021—2023与训练重叠，不称盲测。组合同时对比原基线及组成部分；仅少亏不能证明机制正期望。

| 位置 | 方案 | 区间 | 净收益 | 回撤 | 胜率 | 盈亏比 | 交易数 | 活跃月 | 对基线收益变化 | 对组成部分收益变化 | 额外5bp后 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TFL_original | third_cost3_budget_half | develop | -9.81 | 5.95% | 42.69% | 1.301 | 499 | 36 | 25.76 | 2.10 | -62.80 |
| TFL_original | third_cost3_budget_half | validation | -10.46 | 4.31% | 42.55% | 1.179 | 141 | 12 | -1.63 | 0.00 | -25.02 |
| TFL_original | third_cost3_budget_half | review | -15.21 | 4.55% | 43.82% | 1.156 | 267 | 20 | 16.68 | 0.00 | -48.49 |
| TFL_original | third_cost5_budget_half | develop | -4.95 | 5.95% | 42.92% | 1.309 | 487 | 36 | 30.62 | 6.96 | -55.39 |
| TFL_original | third_cost5_budget_half | validation | -11.28 | 4.48% | 42.14% | 1.186 | 140 | 12 | -2.45 | -0.82 | -25.64 |
| TFL_original | third_cost5_budget_half | review | -13.25 | 4.22% | 43.97% | 1.162 | 257 | 20 | 18.63 | 1.96 | -44.50 |
| TFS_original | third_slope_lock80 | develop | 18.97 | 2.21% | 54.29% | 1.293 | 35 | 19 | 15.64 | 2.55 | 13.92 |
| TFS_original | third_slope_lock80 | validation | 14.22 | 1.62% | 53.33% | 1.760 | 15 | 4 | -0.05 | -4.80 | 12.15 |
| TFS_original | third_slope_lock80 | review | 0.66 | 2.00% | 46.88% | 1.155 | 32 | 11 | -6.03 | 2.30 | -3.98 |
| PBS_original | third_risk_low_reward1.5 | develop | -6.81 | 7.31% | 45.24% | 1.121 | 84 | 21 | 107.56 | 25.89 | -20.88 |
| PBS_original | third_risk_low_reward1.5 | validation | -17.85 | 3.62% | 23.81% | 0.973 | 21 | 8 | 18.82 | -4.71 | -20.85 |
| PBS_original | third_risk_low_reward1.5 | review | 7.77 | 2.16% | 48.94% | 1.241 | 47 | 17 | 0.61 | -3.09 | 0.46 |
| PBS_original | third_risk_low_reward2 | develop | -5.62 | 8.22% | 40.24% | 1.400 | 82 | 21 | 108.75 | 27.09 | -19.55 |
| PBS_original | third_risk_low_reward2 | validation | -15.80 | 3.21% | 23.81% | 1.229 | 21 | 8 | 20.87 | -2.65 | -18.80 |
| PBS_original | third_risk_low_reward2 | review | 20.99 | 2.31% | 46.81% | 1.699 | 47 | 17 | 13.83 | 10.13 | 13.49 |
| PBS_original | third_risk_low_reward2.5 | develop | -2.70 | 8.62% | 37.80% | 1.602 | 82 | 21 | 111.67 | 30.01 | -16.76 |
| PBS_original | third_risk_low_reward2.5 | validation | -13.74 | 2.86% | 23.81% | 1.486 | 21 | 8 | 22.93 | -0.60 | -16.74 |
| PBS_original | third_risk_low_reward2.5 | review | 19.51 | 3.20% | 42.55% | 1.927 | 47 | 17 | 12.35 | 8.65 | 12.09 |
| MRL_original | space_units2_same_unit | validation | 17.42 | 6.10% | 98.17% | 0.031 | 109 | 8 | 40.95 | 5.86 | 3.31 |
| MRL_original | space_units2_same_unit | review | -73.12 | 22.80% | 98.35% | 0.008 | 182 | 17 | 86.85 | 70.87 | -97.65 |
| MRL_optimized | space_units2_same_unit | validation | -1.53 | 9.40% | 97.89% | 0.021 | 95 | 8 | 3.11 | -58.96 | -13.65 |
| MRL_optimized | space_units2_same_unit | review | -69.80 | 22.58% | 98.21% | 0.009 | 168 | 17 | 92.87 | 61.81 | -92.64 |
| MRL_original | space_units2_same_unit | develop | -34.68 | 20.80% | 98.40% | 0.014 | 689 | 29 | 78.98 | -121.31 | -101.40 |
| MRL_optimized | space_units2_same_unit | develop | -48.28 | 23.49% | 98.23% | 0.015 | 621 | 29 | 68.82 | -109.21 | -109.50 |

组成部分：MRL为同预算两层；TFL为单独半预算；TFS为单独均线方向过滤；PBS为单独0.5%风险。所有比较仍需关注回撤变化和实际持仓，详见[完整CSV](third_comparison.csv)。成本压力是固定成交路径扣费，不是重新定仓。

后续须连续2021全历史、邻域与完整八位置交付审计。没有在每个时期同时改善的方案应明确说明取舍，不能隐去失败区间。
