# 本轮方法与依据

上一轮BTC/ETH确认后回踩候选在扣费前合计仍亏损，距离退出的损失超过止盈贡献。继续抬高仓位或降低费用假设不能解决这一现象。本轮检验等待下轨回踩是否错过了持续收复的行情，代价是买价更高、剩余目标更小。

规则要求确认之后的完整一分钟线收盘超过确认线高点，再提交有上限的普通限价单。这样不依赖分钟内部条件触发价，也不假装在下一分钟开盘成交。相较上一轮仍保留相同风险预算与退出公式，但按新入场位置重新计算目标距离。正式回放前固定规则，仅新增一个候选，不按历史结果扫描。

一手资料于2026-09-22核对：

- [NautilusTrader条件限价单](https://nautilustrader.io/docs/latest/concepts/orders/stop_limit/)说明触发后启用限价约束；工程阶段尝试该路径，合成跳空检查发现安装版本可按原触发价成交，即使该分钟最低价更高。正式候选不使用这一撮合路径。草稿和检查保存于`engineering_v1.zip`、`checks_before_audit_extension/`及`checks_before_close_confirmation/`。
- [NautilusTrader K线执行时序](https://nautilustrader.io/docs/latest/concepts/backtesting/bar-execution/)说明已完成K线回调之前先处理该K线的撮合，回调新建订单不能追溯此前高低点。正式候选使用后续已闭合一分钟收盘，在该时点下单；没有延迟和盘口重建，因此仍须成本压力复核。
- [John Bollinger规则](https://www.bollingerbands.com/bollinger-band-rules)说明触轨本身不构成独立交易信号。这不证明本轮高点突破规则具有正收益。

资料用于解释订单及研究机制；是否改善以冻结回放结果为准。多轮已见历史的选择偏差仍存在，任何正结果也不等于未来收益证明。
