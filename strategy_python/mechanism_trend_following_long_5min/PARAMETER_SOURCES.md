# TFL 参数来源与活动状态

必须通过 `_mechanism.factory.create_strategy(asset, key, start)` 加载ETH/BTC对应系数。直接使用配置类的兼容默认值不是本轮冻结候选。

[统计定义](../_optimization/mechanism_20260915/CANDIDATE_DEFINITION.md) · [训练系数及样本数](../_mechanism/coefficients.json) · [原策略逐项来源](../trend_following_long_5min/PARAMETER_SOURCES.md)

| 字段/计算 | 实际作用与来源 |
|---|---|
| mechanism_stop_atr | 24小时内曾达到+1个入场ATR路径中，恢复前逆向幅度/ATR的80%分位。 |
| mechanism_activation_atr | 所有合格机会24小时MFE/入场ATR的中位数。不是已识别趋势拐点。 |
| mechanism_price_risk | 0.005权益价格风险预算；名义比例=min(equity_fraction_per_side,0.005/止损距离)。费用另计。 |
| max_loss_return / take_profit_activation_return | 沿用原类的兼容校验字段；真实退出读取入场冻结快照，不读取这两个旧百分比。 |
| equity_fraction_per_side | 在候选中是名义金额上限，不是每次必用的名义比例。 |
| peak_profit_lock | 保留原0.65，未用本次数据证明其精确最优性。 |
| 小样本回退 | 方向机会或恢复子样本不足100时，按定义使用同资产两方向池；100是设计门槛。 |

| 资产 | 止损ATR倍数 | 启动ATR倍数 | 止损估计来源 |
|---|---:|---:|---|
| ETH | 1.35211958 | 2.38253981 | direction |
| BTC | 1.49029785 | 2.15362884 | direction |

所有系数仅由2021—2023路径估计，交易起点不得早于2024-01-01。保留原信号周期、标准差倍数等字段的原始来源，不把未检验值包装成统计规律。
