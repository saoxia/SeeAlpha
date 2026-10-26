# MRL_original 参数来源与活动状态

必须通过 `_mechanism.factory.create_strategy(asset, key, start)` 加载ETH/BTC对应系数。直接使用配置类的兼容默认值不是本轮冻结候选。

[统计定义](../_optimization/mechanism_20260915/CANDIDATE_DEFINITION.md) · [训练系数及样本数](../_mechanism/coefficients.json) · [原策略逐项来源](../mean_reversion_long_5min/PARAMETER_SOURCES.md)

| 字段/计算 | 实际作用与来源 |
|---|---|
| mechanism_add_multipliers | 训练期成功回到报价中轨的48小时路径中，回归前逆向距离/下轨至中轨距离的25/50/75%分位。分位目标是设计选择。 |
| 动态止盈 | 使用确认订单时的中轨距离；原 take_profit_rate 不再控制此候选的止盈。 |
| mechanism_flat_risk | 消融开关；默认False保留原曲线。 |
| mechanism_flat_multiplier | 训练机会中原强度与位置系数乘积的平均值，只用于常数风险消融，不代表精确逐笔等风险。 |
| 最小加仓距离 | 2×entry_fee_rate，作为双maker费用的近似下限；不保证每笔均按maker成交或费后盈利。 |
| hard_stop_distance/资金预算/恢复规则 | 沿用原风险设计，不宣称由短期分位数推导；25%价格止损保持不变。 |

| 资产 | 加仓带宽倍数 | 常数风险乘数 |
|---|---|---:|
| ETH | [0.30514164961685286, 0.9401591313992598, 2.4167064525201356] | 0.42061586 |
| BTC | [0.30458469520231835, 0.9536560054231893, 2.3278801433036] | 0.45669568 |

所有系数仅由2021—2023路径估计，交易起点不得早于2024-01-01。保留原信号周期、标准差倍数等字段的原始来源，不把未检验值包装成统计规律。
