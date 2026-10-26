# 参数含义的跨时期复核

这些检查检验统计含义能否迁移，不等于检验策略盈利。训练分位数的训练覆盖率接近目标属于定义结果，主要看validation/review。覆盖率使用恢复子样本时，必须同时关注未恢复样本的损失；不能把条件覆盖率当胜率。

## 趋势多头：年度估计

| 资产 | 年份 | 止损ATR倍数 | 启动ATR倍数 |
|---|---|---:|---:|
| ETH | 2021 | 1.3869 | 2.2052 |
| ETH | 2022 | 1.2067 | 2.4086 |
| ETH | 2023 | 1.3990 | 2.4840 |
| BTC | 2021 | 1.3318 | 1.7952 |
| BTC | 2022 | 1.3781 | 2.1026 |
| BTC | 2023 | 1.6686 | 2.6880 |

完整年度系数含MRL、TFS、PBS见[年度参数](annual_coefficients.json)。不同年份因样本数量可能触发回退，不应把来源不同的估计当同一个窄置信区间。

## 训练后统计含义

| 资产/家族 | 区间 | 检查 | 样本数 | 覆盖率 | 月块95%区间 |
|---|---|---|---:|---:|---:|
| BTC/PBS | review | buffer_coverage | 87 | 86.2% | [76.0%, 95.4%] |
| BTC/PBS | review | old_0.25ATR_buffer_coverage | 87 | 87.4% | [77.3%, 95.5%] |
| BTC/PBS | validation | buffer_coverage | 32 | 84.4% | [76.7%, 93.8%] |
| BTC/PBS | validation | old_0.25ATR_buffer_coverage | 32 | 84.4% | [76.7%, 93.8%] |
| BTC/TFL | review | noise_stop_coverage | 266 | 82.3% | [78.5%, 86.1%] |
| BTC/TFL | review | old_0.5pct_stop_coverage | 266 | 67.7% | [62.4%, 72.4%] |
| BTC/TFL | review | activation_cdf | 366 | 49.5% | [45.6%, 53.5%] |
| BTC/TFL | validation | noise_stop_coverage | 145 | 82.8% | [76.3%, 89.1%] |
| BTC/TFL | validation | old_0.5pct_stop_coverage | 145 | 56.6% | [51.6%, 62.8%] |
| BTC/TFL | validation | activation_cdf | 203 | 51.2% | [44.7%, 58.6%] |
| BTC/TFS | review | noise_stop_coverage | 39 | 94.9% | [87.8%, 100.0%] |
| BTC/TFS | review | old_0.5pct_stop_coverage | 39 | 56.4% | [46.1%, 72.4%] |
| BTC/TFS | review | activation_cdf | 60 | 50.0% | [35.5%, 71.9%] |
| BTC/TFS | validation | noise_stop_coverage | 18 | 88.9% | [nan%, nan%] |
| BTC/TFS | validation | old_0.5pct_stop_coverage | 18 | 55.6% | [nan%, nan%] |
| BTC/TFS | validation | activation_cdf | 27 | 55.6% | [nan%, nan%] |
| ETH/PBS | review | buffer_coverage | 80 | 82.5% | [71.9%, 94.0%] |
| ETH/PBS | review | old_0.25ATR_buffer_coverage | 80 | 83.8% | [72.6%, 95.6%] |
| ETH/PBS | validation | buffer_coverage | 43 | 93.0% | [85.4%, 100.0%] |
| ETH/PBS | validation | old_0.25ATR_buffer_coverage | 43 | 93.0% | [85.4%, 100.0%] |
| ETH/TFL | review | noise_stop_coverage | 252 | 77.4% | [72.6%, 82.0%] |
| ETH/TFL | review | old_0.5pct_stop_coverage | 252 | 54.0% | [46.9%, 60.8%] |
| ETH/TFL | review | activation_cdf | 342 | 52.6% | [48.5%, 56.9%] |
| ETH/TFL | validation | noise_stop_coverage | 156 | 85.3% | [78.6%, 91.0%] |
| ETH/TFL | validation | old_0.5pct_stop_coverage | 156 | 64.1% | [55.6%, 71.0%] |
| ETH/TFL | validation | activation_cdf | 211 | 52.1% | [46.2%, 58.1%] |
| ETH/TFS | review | noise_stop_coverage | 56 | 91.1% | [83.6%, 100.0%] |
| ETH/TFS | review | old_0.5pct_stop_coverage | 56 | 42.9% | [32.6%, 51.6%] |
| ETH/TFS | review | activation_cdf | 74 | 50.0% | [37.2%, 62.7%] |
| ETH/TFS | validation | noise_stop_coverage | 23 | 87.0% | [nan%, nan%] |
| ETH/TFS | validation | old_0.5pct_stop_coverage | 23 | 56.5% | [nan%, nan%] |
| ETH/TFS | validation | activation_cdf | 37 | 51.4% | [42.9%, 60.9%] |

NaN区间表示样本少于30或月份少于6，未强行计算。完整MRL加仓分位覆盖率见[数据](mechanism_distribution_checks.csv)。

## 原未知参数可以解释到什么程度

| 参数 | 可检验含义 | 本轮不能声称的结论 |
|---|---|---|
| 趋势0.5%止损 | 恢复前逆向波动的覆盖范围；新方案按入场ATR和条件分位定义 | 不能说0.5%曾经就是按该分位选的，也不能说放宽必定更赚钱 |
| 趋势2%/3%/4%启动 | 在信号后MFE分布中的位置；新方案用典型顺向幅度 | 中位数不是已识别趋势拐点，ETH的优化值不自动适用于BTC |
| MRL 0.5%/1%/2%加仓 | 回归前逆向距离，相对于当时下轨至中轨距离 | 原1:2:4间距未被证明最优；分位接近不证明加仓有正期望 |
| MRL 0.5%止盈 | 与局部均值距离比较，检验固定百分比是否忽略市场状态 | 平均成本变化后的新目标不一定仍等于原中轨，手续费不可忽略 |
| MRL双谷/强度风险曲线 | 条件尾部损失与常数预算消融 | 几个分箱及消融不能证明17%/83%等精确结点有独特依据 |
| PBS 0.25ATR缓冲 | 恢复前超出结构高点的噪声覆盖率 | 80%分位为零不能证明零缓冲最优；90%尾部可能完全不同 |
| PBS 0.4%—3%止损范围 | 结构相对ATR的异常程度 | 中心80%仅是设计约束，不是排除两端必然增益的证明 |
| MRL恢复时间带 | 需要足够多硬止损和完整释放状态事件 | 当前事件不足且释放时点未完整记录，不能恢复精确公式来源 |
| 周期、65%锁利、4R、25%硬止损及资金预算 | 本轮保留的信号/风险设计 | 没有单独证据，不包装成新发现的市场规律 |

最终是否采用候选仍以正式回测和邻域表现判断；即使表现更好，也只能获得可支持的统计解释，不能从历史价格唯一识别原作者当时选数的原因。
