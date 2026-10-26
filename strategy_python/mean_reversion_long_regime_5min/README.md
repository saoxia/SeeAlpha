# 均值回归多头：日线趋势与有限周期研究候选

这是现行BTC均值回归多头的独立研究副本，可在BTC和ETH运行，未替换默认策略。

- 首仓增加完整日线MA52及7天方向过滤，缺口后重新预热。
- 保留原下轨限价入场、止盈、单层定仓和保护止损。
- 24小时或两倍入场布林回归距离触发分钟收盘市价退出；退出后等待回到中轨重新准入。
- 关闭新增开关的baseline必须逐笔复现原版本。

本轮BTC/ETH、四组规则、两档费用共16次完整回放已完成，三种新增规则均未通过预先固定的门槛。组合候选虽将实际最长持仓缩至24小时，但原费率BTC累计净收益为−50.40 USDT，ETH为−97.47 USDT，均比基线更差；费用压力后也未通过。保留研究实现，不替换默认策略。

这些是已见历史的机制诊断，分钟收盘退出不保证亏损或持仓时间硬上限，不能直接用于实盘。

[结果与原因](../_optimization/mrl_regime_20260922/INTERPRETATION.md) · [事先固定研究方案](../_optimization/mrl_regime_20260922/PROTOCOL.md) · [完整结果](../_optimization/mrl_regime_20260922/RESULTS.md)

```bash
.venv/bin/python -m strategy_python._optimization.mrl_regime_20260922.run run BTC_combined_fee0
```
