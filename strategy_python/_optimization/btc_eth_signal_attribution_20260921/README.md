# BTC / ETH 信号与亏损归因

[中文图表报告](report.html) · [文字结果](RESULTS.md) · [事先固定方案](PROTOCOL.md)

这是当前 BTC 均值回归多头和趋势跟随空头的机制诊断。两个市场使用同一组参数；保留全部交易及未实际准入的市场候选。没有新参数搜索，没有把本轮历史当成样本外数据。

## 复现

在项目根目录使用现有环境：

```bash
.venv/bin/python -m pytest -q strategy_python/_optimization/btc_eth_signal_attribution_20260921/study_test.py
.venv/bin/python -m strategy_python._optimization.btc_eth_signal_attribution_20260921.native verify
.venv/bin/python -m strategy_python._optimization.btc_eth_signal_attribution_20260921.native run BTC_MRL
.venv/bin/python -m strategy_python._optimization.btc_eth_signal_attribution_20260921.native run BTC_TFS
.venv/bin/python -m strategy_python._optimization.btc_eth_signal_attribution_20260921.native run ETH_TFS
.venv/bin/python -m strategy_python._optimization.btc_eth_signal_attribution_20260921.audit_replay
.venv/bin/python -m strategy_python._optimization.btc_eth_signal_attribution_20260921.analysis_v2
.venv/bin/python -m strategy_python._optimization.btc_eth_signal_attribution_20260921.report
```

以上入口核对并复用已完整保存的重放。若需要全新计算，在独立副本保留方案和输入，移走该副本的 `runs` 目录后运行；不要覆盖当前归档。已经冻结的 `manifest.json` 不应重写。报告文字判断保存在 `interpretation.json`，不参与信号或交易计算。

ETH MRL 使用补充审计入口：两笔合规限价委托因更低价成交而使最终金额略低于最低门槛，最初按成交金额检查的审计将其误拒绝。详见 [勘误和官方规则来源](AUDIT_AMENDMENT.md)。`audit_amendment` 保留首次输出、失败日志及补充代码冻结记录；完整重复运行要求交易和候选保持一致。该次结果的 `entry_fills_outside_constraints` 旧字段按修正后的委托约束口径计数，原成交价检查的两笔单独保存在 `fill_price_bounds_exception_count` 和明细中。

`analysis_v2.py` 保留原事件和收益计算，只修正一处 pandas 3 的缺失标签处理：终止清仓订单可能没有 `tags`，需先以空字符串填补再分类为 `market_exit_or_end`。第一次分析在 ETH 的终止清仓标签处停止；原 `analysis.py` 和冻结清单不动，修复及此前已生成结果的哈希另存在 `analysis_amendment.json`。重新计算须与已经生成的 BTC 归因和 BTC/ETH 事件结果逐文件一致，不能利用修复改变统计规则。

## 文件

- `manifest.json`、`frozen_sources.zip`、`archive.json`：运行之前冻结的研究计算、策略、数据、基线引用和环境。
- `native.py`：只记录观察值的原策略子类，所有下单、状态更新仍调用原实现。
- `runs/{BTC,ETH}_{MRL,TFS}`：参数、原生成交/账户/交易/权益、完整性摘要。MRL 目录额外保存该市场预热及全期一分钟 OHLC。
- `decisions.parquet`：MRL 每次下轨报价或 TFS 通过市场条件的交叉，包含原账户状态、定仓结果和开仓门槛准入结果。MRL `risk_sizing_unavailable` 包含风险系数使数量降至零等情况；没有放大仓位凑最低金额。
- `analysis/*_candidates.parquet`：含未实际成交机会的原始市场候选，MRL 触价是研究代理，不是原生成交。
- `analysis/*_s*_*.parquet`、`*_matches.npz`：逐事件收益和精确对照索引，索引对应各市场 `market.parquet` 的行号。
- `analysis/signal_summary.csv`、`signal_annual.csv`：两种间隔、三个观察窗口、全期/近期和逐年结果；MRL 同时报普通及同触价机制对照。
- `analysis/*_all_trades.csv`、`*_all_losses.csv`：所有交易和所有亏损交易，含现金流、持仓时间、原始路径极值、固定时限退出。
- `analysis/exit_size_comparison.csv`：同一批入场的原退出 / 1h / 4h / 24h，等名义金额及恢复实际数量，两档固定路径成本。
- `execution_audit.json`、`final_validation.json`：三组原有交易基线逐笔对齐、现金流/最低金额/步长审计、输入不变检查。

## 解读约定

1 bp = 0.01%。信号相对优势是相对匹配对照的**毛收益差**，不等于可执行策略收益。MRL 事件从触价分钟收盘开始，不包含该分钟内的限价反弹收益。

定时退出保留原入场队列，可能出现持仓重叠；`fixed100_sum_pnl` 是每笔独立投入 100 USDT 后的收益合计，`original_size_sum_pnl` 是按原交易数量计算的收益合计。两者都不能直接解释为共享账户净值或可实现组合收益。

“原退出”的全队列收益与原生账户对齐；在定时退出对照表中，所有退出方式统一使用有完整未来数据的共同子队列，可能与全队列净盈亏不同。近期事件和反事实按入场时间划分，原生近期绩效按连续盯市权益划分，不能混淆。

原生交易的路径 MFE/MAE 排除入场分钟，但包含退出分钟的高低价，无法进一步区分该分钟内的退出前后，属于分钟路径代理。现金流盈亏使用实际模拟成交价，不受这一极值近似影响。

区块区间、样本数门槛和双市场门槛属于预先定义的诊断筛查，不能补救历史多次选参偏差。缺少资金费率、真实滑点、订单队列和逐日历史合约规则；未据此给任何版本实盘准入。
