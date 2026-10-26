# BTC五分钟均值回归做空

从当前ETH `mean_reversion_short_5min` 冻结迁移，更换包名、类名和BTC合约标识，并在执行层校验开仓数量/名义金额；未按BTC收益选参。
这是布林上轨限价做空、最多两层、分段退出的均值回归策略；与小时反弹失败做空不同。

原规则与参数保持一致，包括30%周期风险预算、均价上方60%价格硬止损和数据就绪条件。
这些参数是迁移对照，不代表已通过BTC收益或风险验证。历史ETH详细规则见
[来源说明](../mean_reversion_short_5min/README.md)，冻结源码哈希见[迁移记录](migration.json)。

独立入口：`.venv/bin/python -m strategy_python.btc_mean_reversion_short_5min.backtest --start 2022-05-01 --end 2022-06-01`。
统一入口：`.venv/bin/python -m strategy_python.btc_backtest mean_reversion_short --start 2022-05-01 --end 2022-06-01`。
默认只用已有本地数据；账户500 USDT、杠杆5倍、15天预热。实测结果另存，不借用其他空头策略的历史收益。

执行核验中发现引擎保证金账户跳过最低名义金额检查，已补充开仓约束并修正BTC订单标签。
修正前全历史结果仅保留为缺陷诊断；修正后全历史回测单独对账，详见[整理记录](../_optimization/btc_consolidation_20260921/RESULTS.md)。

修正后2021-01-01至2026-08-30连续回放：716个持仓，累计亏损60.02%，最大回撤62.64%；2023年12月后余额不足以满足当前最小开仓条件。详见上述整理记录。
