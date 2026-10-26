# 均值回归多头：成本与收复确认研究

独立研究包，支持BTC/ETH相同规则。10组新增回放已完成：回撤改善，但没有形成跨市场一致的收益改善，未通过替换门槛。详见[结论与原因](../_optimization/mrl_entry_20260922/INTERPRETATION.md)。

研究预设保留原单层仓位预算和执行约束，新增：

- 预计回归空间至少覆盖两倍保守往返成本，按实际费率和价格精度计算。
- 完整5分钟收盘跌破此前下轨，下一根收回同一冻结下轨后，才允许回踩挂单。
- 报价以原生GTD订单在5分钟后到期；不复用过期或跨缺口的确认。
- 沿用24小时／两倍入场回归距离退出及中轨重新准入；不启用上一轮日线过滤，不因止损更近增加仓位。

`cost`只有成本门槛，`reclaim`加收复确认，为主候选；`control`仅供验证关闭开关后与旧bounded一致。

[方案与判断门槛](../_optimization/mrl_entry_20260922/PROTOCOL.md) · [方法依据](../_optimization/mrl_entry_20260922/METHODS.md) · [完整结果](../_optimization/mrl_entry_20260922/RESULTS.md)

```bash
.venv/bin/python -m strategy_python._optimization.mrl_entry_20260922.run run BTC_reclaim_fee0
```

本包不接入默认四策略入口或现有90天观察。成本空间和价格确认都是待检验假设，不代表预期收益为正。
