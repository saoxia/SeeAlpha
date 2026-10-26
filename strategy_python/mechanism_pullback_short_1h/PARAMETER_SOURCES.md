# PBS 参数来源与活动状态

必须通过 `_mechanism.factory.create_strategy(asset, key, start)` 加载ETH/BTC对应系数。直接使用配置类的兼容默认值不是本轮冻结候选。

[统计定义](../_optimization/mechanism_20260915/CANDIDATE_DEFINITION.md) · [训练系数及样本数](../_mechanism/coefficients.json) · [原策略逐项来源](../pullback_short_1h/PARAMETER_SOURCES.md)

| 字段/计算 | 实际作用与来源 |
|---|---|
| mechanism_buffer_atr | 达到+1ATR前，超出结构高点的噪声/ATR的80%分位；本次两资产均为0，实际仍保留至少1tick。 |
| mechanism_min_stop_atr / mechanism_max_stop_atr | 训练结构距离/ATR+缓冲的10/90%分位，约束异常结构；中心80%是设计选择。 |
| stop_atr_buffer / min_stop_fraction / max_stop_fraction | 原类兼容校验字段，不再控制候选的结构过滤和缓冲。 |
| risk_fraction / max_notional_fraction | 工厂固定0.015/3；所有正式PBS旧版对照也统一此预算。 |
| reward_risk / max_holding_hours | 保留4R/24小时，不宣称本轮重新推导出这些数值。 |

| 资产 | 缓冲ATR | 最小结构ATR | 最大结构ATR |
|---|---:|---:|---:|
| ETH | 0.00000000 | 1.07308814 | 2.62407262 |
| BTC | 0.00000000 | 1.02438477 | 2.53922421 |

所有系数仅由2021—2023路径估计，交易起点不得早于2024-01-01。保留原信号周期、标准差倍数等字段的原始来源，不把未检验值包装成统计规律。
