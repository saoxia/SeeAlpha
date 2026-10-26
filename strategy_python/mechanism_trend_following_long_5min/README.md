# TFL 条件路径机制候选

这是独立研究候选，支持ETH/BTC；已完成正式区间评估，结果不支持整体替换原策略。详见[研究结论](../_optimization/mechanism_20260915/CONCLUSIONS.md)。

[新参数逐项说明](PARAMETER_SOURCES.md) · [统计定义](../_optimization/mechanism_20260915/CANDIDATE_DEFINITION.md) · [冻结系数](../_mechanism/coefficients.json) · [研究诊断](../_optimization/mechanism_20260915/DIAGNOSTICS.md)

必须通过 `strategy_python._mechanism.factory.create_strategy(asset, "TFL", start)` 使用对应资产的冻结系数，交易起点不得早于2024-01-01 UTC。

独立短回测：

```bash
.venv/bin/python -m strategy_python._mechanism.backtest BTC TFL --start 2024-08-01 --end 2024-09-01
```

长区间使用连续分批入口：`python -m strategy_python._optimization.mechanism_20260915.run BTC_TFL review`。

原配置中的历史百分比注释保留来源用途。趋势实际止损/启动/名义比例来自每次入场冻结的ATR快照；MRL实际加仓/止盈来自确认订单的BOLL距离快照；PBS实际缓冲/止损过滤使用结构ATR单位。相应旧百分比字段不再控制候选的这些决策。其他信号、时间、预算与成本约定见研究定义。

趋势新方案用同一统计定义替换旧原版/优化版的不同启动百分比，因此只有一个机制候选。MRL仍分别保留2σ与2.25σ信号候选。PBS工厂统一风险1.5%、名义上限3倍，比较旧策略时亦统一预算。

这次改单快照和价格精度修复服务于正确回测；尚未承诺实盘交易所异步网络行为、部分成交及融资规则的完整认证。
