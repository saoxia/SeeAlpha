# 收复后持续走强研究

[固定方案](PROTOCOL.md) · [方法依据](METHODS.md) · [完整结果](RESULTS.md) · [结论解释](INTERPRETATION.md) · [权益曲线](report.html)

6组新增回放与10组参考核验已完成。两市场回撤下降，BTC全期亏损减少；ETH原费率和部分近期结果退化，6组全期／近期仍全部为负，未通过替换门槛。全项目1,203项测试通过，[最终验收](acceptance.json)记录完整性与运行状态。

唯一新候选将下轨回踩入场改为后续完整一分钟收盘突破确认线高点后入场，保留成本与仓位约束。两市场、两档费用加严格成交共6组新增回放，另核验10组原策略及上一轮候选记录。所有结果保留，判定同时比较原策略和上一轮，不能仅击败其中较差的一组。

```bash
.venv/bin/python -m strategy_python._optimization.mrl_recovery_20260922.run verify
.venv/bin/python -m strategy_python._optimization.mrl_recovery_20260922.run all --workers 3
.venv/bin/python -m strategy_python._optimization.mrl_recovery_20260922.run report
.venv/bin/python -m strategy_python._optimization.mrl_recovery_20260922.diagnostics
```

运行在`runs/`，日志在`logs/`，源码和数据清单在`manifest.json`；已完成记录只有内容和源码哈希均一致才复用。每组保存入场决策、订单、成交、持仓、账户、逐年及日末权益，另外按分钟盯市核算最大回撤。`entry_decisions.csv`可复核完整确认、后续分钟与报价上限的因果顺序。诊断中的月收益区间仅为描述性统计，不修正多轮历史选择偏差。

工程阶段先验证原生条件限价路径，合成跳空暴露了可能过于有利的触发价成交假设；正式回放前改为完整一分钟收盘确认后提交普通限价单，避免该路径。初稿源码在`engineering_v1.zip`，早期检查在`checks_before_audit_extension/`和`checks_before_close_confirmation/`，最终工程检查在`checks/`。这些修订没有根据短区间收益选参数。原有研究和观察全部保持冻结。
