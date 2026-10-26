# 最终完成状态

正式session88967已退出0，main76项+neighbors36项全部完成。以下旧进度保留作执行历史，不再表示仍有后台任务。

- FORMAL_RESULTS.md、formal_comparison.csv/json、formal_order_events.csv、mechanism_equity.png、formal_verification.json已生成并核验；图已目视检查。
- 112项正式回测、14项运行验证、4家族流式完全一致；28项测试通过；335个冻结源码及4868个市场源文件核对。
- 120项条件分布检查、24组趋势成交结构、恢复样本审计及5包参数来源说明齐全。
- CONCLUSIONS.md已生成，包括BTC新/固定/比例对照、ETH比较链接、常数风险消融、邻域范围及成本限制。
- 主要结论：不支持整体替换旧策略。BTC近期常数风险明显改善但2024亏损，ETH近期同项明显恶化；不能视为跨资产通用规律。趋势止损/启动邻域出现盈亏反转，成本可抹去小额盈利，不能声称排除过拟合。
- 原有策略未覆盖，主参数未按后期收益回选。候选是独立研究产物，未知精细参数仍保留未知。

---

# 参数机制研究：当前状态与继续执行

目标仍进行中，不能标记完成。上一目标轮和本轮均有实质进展：事件/统计方案已完成，本轮已接入实际策略并启动正式原生回测。

## 当前运行任务（优先重新核验，不能盲目重启）

正式总进程 `python -m strategy_python._optimization.mechanism_20260915.run_all` 正在运行，exec session **88967**。日志 `all_formal.log`；当前main阶段看 `main_evaluate.log`，逐项日志以key_period_variant_mode命名。

run_all顺序运行main、neighbors，每阶段最多两个原生引擎。main共76项（68主候选/原/优化/比例对照+8 MRL常数风险消融）；neighbors共36项；总计112项。每项完结标志metrics.json，缓存会核对参数和源码。明确等待现有session或进程，不能仅因日志一时不变重启。

## 已完成的权威产物

- PROTOCOL.md、CANDIDATE_DEFINITION.md：机制定义和完整交付要求。
- ETH/BTC分钟缓存和完整5/15/60/1440分钟聚合、源文件哈希。ETH36630事件/109204标签，BTC36773事件/109570标签，共218774路径。
- DIAGNOSTICS.md、原参数分位数、风险形状、PBS结构过滤CSV。
- candidate_coefficients.json只用2021—2023，annual_coefficients.json有逐年系数。
- `strategy_python/_mechanism/`：生产纯规则、冻结系数、factory、独立backtest入口；五个独立mechanism_*策略包，支持ETH/BTC（共10主候选）。旧ETH/BTC/btc_scaled源文件保留。
- 趋势按入场ATR冻结止损/启动，名义仓位=min(1,0.005/止损距离)；只支持默认单仓交叉/峰值退出。MRL按确认订单快照冻结带宽、加仓距离、止盈，定仓同步使用；PBS结构ATR范围+缓冲，预算统一1.5%/3倍。
- `runtime_tests.log`：**28 passed**（生产工厂、纳秒/缺口、冻结规则、改单拒绝/确认、成交先于确认、延迟加仓平仓、架构）。
- `runtime_verification.json`：**14项下跌月验证成功**，4家族普通/7天流式成交完全相同，现金流核对/价格步长通过。月份2024-08。所有10资产/候选均实际成交。
- `frozen_runtime_sources.json`：当前正式版本源码冻结，包括旧对照和共享机制文件。运行期间不要改策略、共享机制文件或run.py。
- `run.py`：资产感知原生连续90天流式，validation=[2024-01-01,2025-01-01)，review=[2025-01-01,2026-08-31)。统一500USDT/5倍保证金杠杆，PBS全部统一1.5%风险/3倍名义上限。
- `runtime_strategies.json`：34个主注册项：10机制、16旧原/优化(ETH/BTC各8)、8BTC波动比例版。
- `report_runtime.py`已编写，尚未正式执行。要求112项全部完结，检查hash/缺口/现金流/价格，输出FORMAL_RESULTS.md、formal_comparison.csv/json、formal_order_events.csv、mechanism_equity.png、formal_verification.json。新增统计延迟加仓平仓次数，避免全归因于参数。需实际运行检查报告和图，不可因脚本存在就视为完成。
- `RECOVERY_EVIDENCE.md`和recovery_event_counts.json：用hard-stop标签核对Q3。ETH原/优化训练仅10/9个硬止损持仓，BTC全历史10/9；ETH优化全历史17。原/新资产事件重叠、缺完整恢复释放时点，因此不能拟合26+1.5d等精细时间带。

## 已修复并重验的运行问题

1. 浮点tick误差会让PBS的高点+1tick取整回高点；改成Decimal加法，增加真实BTC价格刻度网格测试。早期探测结果移到runs/debug_before_freeze，后14项已重新运行并验证。
2. 第一次正式批次在ETH MRL review发现：旧周期平仓后延迟加仓成交，cycle_snapshot为空。当前新版将加仓快照写入订单metadata；若平仓后迟到加仓，立即提交reduce-only orphan_exit，不新建alpha周期，不触发额外hard-stop恢复锁。改单pending风险也计入预留风险。
   - 单元测试覆盖迟到成交处理。
   - 受影响的14项验证已重跑（非MRL复用有效缓存），verify_runtime重新通过且重新冻结源码。
   - 失败正式结果/旧验证移到runs/debug_before_orphan_fix；日志和旧冻结manifest在debug_logs/orphan_failure。
   - 初次正式session12261已明确退出1，无遗留任务。当前88967为修复后的正式总进程。
   - 当前ETH_MRL_original review已完整完成并通过对账，净收益-25.90420122USDT；原失败已在完整历史复现中验证修复。正式任务继续其他候选/对照。

## 接下来必须完成

1. 轮询session88967或核对其真实进程状态，继续现有任务直到main/neighbor全部完成。有失败时读精确日志，修复后只重跑受影响源码范围，保留失败证据；勿把缓存/日志当活进程。
2. 运行report_runtime并核验112项范围、各对照预算、流式验证、图像。脚本若假设错误需修复报告，不改冻结交易参数。
3. 汇总参数证据映射：Q1/Q2条件风险形状及常数曲线实测；Q3样本不足；Q6带宽归一化加仓两资产相近但是否盈利待结果；Q9止损恢复噪声；Q12/Q13典型MFE启动与小样本回退；Q10结构ATR缓冲；未研究/风险偏好值不能编造统计来源。
4. 主方案与ETH/BTC旧原/优化及BTC比例版，2024/近期、收益/回撤/费用/邻域稳定性都要解释；不能只挑胜出候选。趋势同时调整仓位，MRL同时修复执行问题，归因需保留限制。
5. 添加最终入口/结果说明，更新旧WORKLOG/诊断的“尚未接入/回测”状态但保留历史记录。完整执行目标审计后再update_goal complete。

## 使用与注意

- 短区间：`python -m strategy_python._mechanism.backtest BTC TFL --start 2024-08-01 --end 2024-09-01`，help已验证。
- 生产factory默认2024起点并拒绝更早；训练期不能作无前视候选回测。
- 趋势旧原/优化的百分比启动被统一统计定义替代，所以只一个TFL/TFS机制候选；MRL保留2σ/2.25σ；PBS固定同预算。
- PBS buffer70与主buffer80均为0，不重复跑；buffer90是独立邻域。所有邻域只报告，不按后期收益回选主方案。
- 运行时数据只读，未动旧策略。禁止在活跃正式回测期间重新运行build_runtime.py（它是重建工具，会覆盖候选源码）。
- 尚未认证实盘异步网络/完整部分成交场景，当前交付定位回测研究，不应宣称实盘就绪。

## 本轮补充（正式进程仍在运行）

- 主对照68项已完成，常数风险消融进行中，之后由同一session88967自动运行36项邻域。不要重复启动。
- mechanism_evidence.py已完成120项训练/跨时期分布检查，MECHANISM_EVIDENCE.md及mechanism_distribution_checks.csv。
- trade_shape.py已完成24组实际趋势成交结构并与净收益对账，TRADE_PAYOFF_EVIDENCE.md。
- recovery_evidence.py应急标签修正为实际 :emergency: 后重跑，计数仍为0，硬止损数量不变。
- 五个候选PARAMETER_SOURCES.md及README链接已写入，说明活动参数与旧兼容字段。
- report_runtime.py新增原始行情/缓存哈希、训练定义/运行验证检查。
- summarize_results.py已编写：待formal报告112项核验完成后运行，生成CONCLUSIONS.md。尚未生成最终结论文件。
- 报告脚本Ruff通过；335个冻结运行源码哈希未变。
