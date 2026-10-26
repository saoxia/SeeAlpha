# 入场背景诊断：原实际交易

只使用严格早于首笔成交时间的完整小时数据，遇缺口重置ATR/MA；这是保守的成交时背景近似，不是精确复现此前限价报价时特征。超过2小时陈旧背景及跨开发/检验边界持仓已排除。波动分箱仅按开发期三分位划分。

仅用于提出假设：筛选后交易不再保证仍有原持仓互斥、退出与资金路径，不能把分组净收益当新策略回测或统计显著结论。下一步必须使用当时真实可见特征进行原生策略验证。

[分组数据](entry_context_summary.csv) · [排除计数](entry_context_exclusions.json)

| 基线 | 开发期状态 | 样本 | 单位名义平均净收益 | 净收益 |
|---|---|---:|---:|---:|
| BTC_MRL_original | aligned_ma99=False | 139 | -0.105% | -51.33 |
| BTC_MRL_original | aligned_ma99=True | 778 | 0.138% | -65.74 |
| BTC_MRL_original | aligned_slope6=False | 61 | 0.307% | 24.39 |
| BTC_MRL_original | aligned_slope6=True | 856 | 0.086% | -141.46 |
| BTC_MRL_optimized | aligned_ma99=False | 115 | -0.170% | -51.00 |
| BTC_MRL_optimized | aligned_ma99=True | 650 | 0.138% | -66.31 |
| BTC_MRL_optimized | aligned_slope6=False | 50 | 0.351% | 22.54 |
| BTC_MRL_optimized | aligned_slope6=True | 715 | 0.073% | -139.85 |
| BTC_TFL | aligned_ma99=False | 213 | -0.145% | -16.43 |
| BTC_TFL | aligned_ma99=True | 291 | -0.038% | -30.56 |
| BTC_TFL | aligned_slope6=False | 261 | -0.023% | 5.46 |
| BTC_TFL | aligned_slope6=True | 243 | -0.148% | -52.45 |
| BTC_TFS | aligned_ma99=False | 2 | -0.434% | 0.56 |
| BTC_TFS | aligned_ma99=True | 46 | 0.244% | 2.77 |
| BTC_TFS | aligned_slope6=False | 14 | -0.778% | -15.44 |
| BTC_TFS | aligned_slope6=True | 34 | 0.625% | 18.78 |
| BTC_PBS | aligned_ma99=False | 15 | -0.200% | 15.56 |
| BTC_PBS | aligned_ma99=True | 68 | -0.315% | -129.93 |
| BTC_PBS | aligned_slope6=False | 54 | 0.147% | -25.21 |
| BTC_PBS | aligned_slope6=True | 29 | -1.115% | -89.16 |
