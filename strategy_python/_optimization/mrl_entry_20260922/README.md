# 入场质量优化研究

[结论与原因](INTERPRETATION.md) · [方法选择](METHODS.md) · [事先方案](PROTOCOL.md) · [完整结果](RESULTS.md) · [曲线与指标](report.html)

已完成全部10组新增回放和8组参考核验。主候选降低了回撤、减少了费用压力下的亏损，但原费率全期收益低于原策略，未通过替换门槛。全项目1,186项测试通过；最终完整性及运行状态见[验收记录](acceptance.json)。

两项新增规则按固定顺序对照：成本门槛；成本门槛加跌破收复确认。BTC/ETH、原费率/+5bp两档，共8次完整原生回放；主候选加2次严格成交复核。另核验并复用上一轮original/bounded共8组参考，不把击败较差对照当成击败原策略。

规则与门槛固定后，先做2022年5月工程检查，随后冻结正式输入。工程阶段修复策略重置时旧退出标记未清空的问题，初次检查保存在`checks_before_reset_fix/`；最终代码重新进行控制组逐笔对照和确认组到期审计，记录在`checks/`。未据此调整交易参数。

复核入口：

```bash
.venv/bin/python -m strategy_python._optimization.mrl_entry_20260922.run verify
.venv/bin/python -m strategy_python._optimization.mrl_entry_20260922.run all --workers 4
.venv/bin/python -m strategy_python._optimization.mrl_entry_20260922.run report
.venv/bin/python -m strategy_python._optimization.mrl_entry_20260922.diagnostics
```

完整运行在`runs/`，日志在`logs/`；已完成且哈希一致的结果会复用。`manifest.json`记录来源、数据、旧对照和环境；`frozen_sources.zip`保留源码。结果包含成交、账户、交易、日末权益、年度指标及逐项判定。

独立描述性诊断保存在`payoff_diagnostics.csv`、`exit_contributions.csv`和`monthly_uncertainty.csv`。后者使用3个月循环区块、2000次重采样，展示全期和近期平均月收益的近似区间；2026年8月不完整，且区间没有修正已有历史选择偏差。它不改变冻结判定，不代表未来盈利概率。诊断脚本和输出另有`diagnostics_validation.json`记录哈希。

`entry_admission`是通过原市场及持仓条件后的准入检查次数，尚未排除定仓和最低委托金额，不等同于成交数。过滤降低频率不自动构成改善；零交易及小样本必须按协议标记。价格确认后的回踩可能有不利选择效应，严格成交仅是敏感性复核，不模拟真实队列。

全部历史已参与研发。默认策略及原前向观察保持原样，是否值得独立前向验证由完整门槛决定。
