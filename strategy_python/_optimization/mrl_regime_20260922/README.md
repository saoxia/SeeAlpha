# 均值回归多头：固定机制对比

独立研究包为 `strategy_python.mean_reversion_long_regime_5min`。四组规则为原基线、仅日线过滤、仅退出规则组、两者组合；组合为事先指定主候选。BTC/ETH使用相同参数，并实际重跑每次成交额外5bp的成本压力，共16次完整回放。

[结果解释](INTERPRETATION.md) · [研究方案](PROTOCOL.md) · [工程修订](AMENDMENTS.md) · [完整结果](RESULTS.md) · [图表](report.html)

## 复核

使用项目现有环境，在项目根目录运行：

```bash
.venv/bin/python -m strategy_python._optimization.mrl_regime_20260922.run verify
.venv/bin/python -m strategy_python._optimization.mrl_regime_20260922.run all --workers 3
.venv/bin/python -m strategy_python._optimization.mrl_regime_20260922.run report
.venv/bin/python -m strategy_python._optimization.mrl_regime_20260922.diagnostics
```

已经完成且哈希一致的回放会复用；来源、数据、环境或结果变化会报错，不静默覆盖。完整结果在 `runs/`，进度和失败原因在 `logs/`。单组入口例如 `run BTC_combined_fee0`；短工程检查入口为 `check BTC_combined_fee0`，只覆盖2022年5月，不是正式绩效结果。

`manifest.json`固定58个来源文件、4878个数据文件、历史参考结果及环境。`frozen_sources.zip`保存源码，`archive.json`记录归档和清单哈希。每次回放前后核验输入，每份结果附有文件哈希；报告另保存`final_validation.json`。

首次冻结后发现空交易表统计错误，在正式全区间回放前修订统计工具。首次清单、源码和工程检查保留在`revisions/v1/`；策略规则、参数及方案未修改，详见修订记录。

## 阅读结果

- `comparison.csv`列出全部16组收益、分钟盯市回撤、费用、交易数、近期表现和持仓时间。
- `decision.json`逐项记录跨市场、跨费用门槛，其他组通过不替代组合主候选。
- 各组的`trades.csv`、`fills.csv`、`account.csv`和`annual.csv`保留成交、账户和年度证据；`metrics.json`含现金流对账与订单审查。
- `payoff_diagnostics.csv`和`exit_contributions.csv`从冻结成交记录重新分解盈亏与退出原因，逐组核对交易数和净收益。原回放器的附加`exit_order_tags`计数未统一列表标签及横线命名，不能用作退出次数；应使用这里核验过的CSV及`diagnostics_validation.json`。该显示问题不参与收益、回撤、交易数或通过判断，冻结回放文件保留原样。
- 日线过滤计数包含预热期及未满足其他入场条件的检查，不等于被拦截交易数。零交易的胜率、盈亏比为空，收益为零，不属于盈利证据。
- 图表是日末权益；最大回撤按分钟盯市，不能只凭图中曲线估算。

历史已用于研发，本轮不能算独立样本外。未计资金费率、真实排队、盘口滑点及逐日合约规则。默认四策略和现有90天观察继续按原方案运行。
