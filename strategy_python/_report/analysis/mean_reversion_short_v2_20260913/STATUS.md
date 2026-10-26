# 已完成原生回测验证：全历史正收益，近期负收益

最终策略 mean_reversion_short_v2_5min，五分钟信号/均值目标，UTC周末建仓，3～5倍开仓名义上限。
默认3倍，单笔计划风险2%，不加仓，12小时市价退出。全期3倍+70.65 USDT、5倍+70.89 USDT；
2025年以来独立3倍回测-11.52 USDT。不能称作稳定盈利或样本外验证通过。
详见结果说明.md、signal_and_risk_audit.json和native_*_audit.json。
原selection.json全天候候选已淘汰；selection_weekend.json才是当前默认参数来源。
