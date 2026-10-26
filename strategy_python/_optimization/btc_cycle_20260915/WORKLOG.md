# 当前目标进行中：BTC八位置总子时空循环优化

## 目标不能缩减或提前完成

用户要求最新机制BTC八策略优化，旧代码不改，新建优化包，按总→子→时→空反复循环得到更好的PnL曲线。当前只完成总诊断、候选框架、行为验证并启动第一轮，远未达到完整目标，不可update_goal complete。

八位置映射暂按四家族×原/优化：MRL两个不同BOLL基线，TFL/TFS/PBS各两位置共用各家族最新机制基线（总计五种不同基线）。已通过异步提问请求可选确认和风险取舍，尚无答复时按8位置+收益提高且回撤不恶化推进，不重复询问。后续纠正纳入当前工作。

## 当前活进程（优先核实，不可盲目重启）

`.venv/bin/python -m strategy_python._optimization.btc_cycle_20260915.evaluate round1`，exec session **26223**，日志round1.log。最多两原生引擎；38项2021—2023开发区间：MRL原/优化各7，TFL/TFS各9，PBS6。基线相同的重复位置开发期共享证据，不假称独立实验；最终8独立包仍需实际验证。

smoke session99912已退出0，10项完成。验证session41808退出0，SMOKE_VERIFIED10。切勿重复启动已运行阶段。运行时不要修改packages、run.py、factory.py、strategies.json；已记录frozen_runtime.json。

## 已完成的证据

- PROTOCOL.md：完整目标、时空含义、选择原则、第一轮假说，旧代码不改。
- prior_files_sha256.json：1517个此前.py/.json文件哈希，smoke后均未变。
- diagnose.py生成TOTAL_DIAGNOSIS.md、total_diagnosis.csv、holding_diagnosis.csv及五基线逐持仓CSV。
  - MRL97%胜率，金额盈亏比约0.02/0.017，最长持仓2424h；>168h持仓累计亏519/477USDT，短期多数盈利。这是事后诊断而非可交易筛选。
  - TFL小于6h交易累计亏269USDT，费用总207USDT；不把未来持仓时长作为入场条件。
  - PBS最短时长组胜率8%，有快速止损问题；长持仓组盈利不能证明延长持仓必有用。
- packages/下8独立研究候选包，原有源文件完全未动。build.py可重建但运行中禁止执行。
- 新规则默认关闭；MRL支持真正时间退出与可见收盘价相对冻结BOLL距离的结构退出，不改变原定仓止损预算。cycle_exit不当作灾难硬止损开启恢复锁，有撤单、在途退出去重和重试；禁止退出时重新加仓。
- 趋势支持5/15/30分钟信号聚合（小时ATR/背景保持）、持仓时限、典型顺向距离与0.1%往返taker费用之比过滤。单腿快照约束仍保留，不能直接多腿。
- PBS增加小时MA下降过滤；时间/目标采用现有参数。
- run.py独立原生90天连续分批，支持develop/validation/review/full/stress及实际account_leverage实验值。experiments.json按场景存完整overrides；create_strategy通过独立factory加载冻结机制系数。
- 8配置默认参数与原基线逐项一致；smoke_verification.json记录5不同家族原生成交/收益/费用/回撤完全一致、10场景价步长和账目通过、MRL time24实际触发且最长持仓<=24h+1min。
- test_cycle_exit.py三个边界行为测试通过（到期、不提前；冻结带宽与当前可见价；关闭功能/退出重试重置）。不要无故重复测试。
- Ruff已修复新包与runner导入；运行源码冻结后不可修改以免缓存失效。build.py生成器未同步Ruff格式但逻辑一致，只有必须重建时再处理。

## 下一步必须继续

1. 确认session26223 live，等待第一轮38项完成；若异常读取对应case日志，只修复有依据的缺陷，重跑受影响范围，不能因日志不变重启。
2. 写/执行round1汇总与筛选脚本：全部结果/年度/胜率/盈亏比/成本/持仓时长及收益回撤与main比较。选择规则在查看新结果前尽量明确，避免无约束挑最好；费用或零成交改善不是机制改善。先筛开发期，再跑2024和近期，全部历史已看过不称盲OOS。
3. 对有证据的点做第二轮组合/邻域，至少再次总诊断；第一轮失败则回到假设，不就此把任务标完成。
4. 「时」含真实信号/持仓/冷却时间及时间区间。15/30分钟改变信号条件分布，原系数不应再宣称具原5分钟的同一统计覆盖含义；必要时后轮再训练校准。
5. 「空」做实际账户杠杆和实际仓位预算/名义上限、MRL max_units对照。账户杠杆和暴露分开，不能仅缩放曲线；趋势目前单腿，最大持仓先按名义上限，若多腿需新增各腿独立snapshot和测试。
6. 每个8位置有可追溯独立最终新包/配置、旧版同条件实际验证、全2021—2026曲线/年度、2024/近期、成本/邻域、活跃性、风险指标；不保证全部盈利但不能降低目标为仅框架或仅一项改善。未达成则继续目标。
7. 最终核对1517个旧文件不变，以及实际交付范围，不把测试通过当策略优化成功。

## 其他重要上下文

此前机制研究112项和2021全历史7项已完成，结果在mechanism_20260915与mechanism_full_20260915。它们属于上个已完成目标，不能把它们当新目标完成。

全历史机制系数使用2021—2023训练，属于含训练期回放，不是滚动训练；后段历史也已看过。当前新增目标仍必须诚实标注此限制。

空阶段可用趋势mechanism_price_risk和equity_fraction_per_side，PBS risk_fraction/max_notional_fraction，MRL cycle_risk_fraction/max_units。max_units减少会重分配同预算到较少腿，不能误称单位仓位不变。若增加新字段，复制到下一轮新包或结束现阶段后明确重冻，不修改运行中源码。

## 本轮追加

- ROUND1_SELECTION.md已在读取新开发结果前写定；round1_report.py已编写并Ruff通过，尚未执行，需38项全部完成。候选不足活动门槛不通过；无同时改善时只挑回撤或额外成本结果改善的诊断候选，不能接受。
- baseline_curves.png已生成并目视检查，TOTAL_DIAGNOSIS.md有日末PnL和每日回撤链接。
- 首个MRL开发基线已完成，time24和time48在运行；以实时session26223核实为准。
- 当前目标轮属于实质进展：新增证据/八包/三行为测试/十项原生验证及38项阶段启动；目标仍未完成。

- 已读第一项开发结果：MRL time24净亏245.50/回撤56.44%，基线净亏113.66/回撤41.60%；交易923→1816，费用69.11→124.76，单纯到期退出假设未通过。90次time_exit，下一次入场间隔已按成交时间排序计算存time24_failure_evidence.json。不要把短期smoke的盈利当优化成立，也不要直接假设重入都发生在几分钟内。其余36项仍在跑，保持预定网格。

## 本轮新证据与下一步指向

- entry_context_audit.py已修复pandas时间精度并生成130项背景分组，ENTRY_CONTEXT.md。保守使用早于成交的小时数据，跨分割持仓排除；不能当新策略回测。TFL/PBS未普遍支持更长均线同向过滤；TFS有方向一致分组改善线索但样本小。
- lot_constraint_audit.py/LOT_CONSTRAINT.md给出重要空间缺陷：flat MRL2025预算66.61/57.11USDT，最便宜0.001 BTC四层风险约75.95/75.52，所有报价均无法下最小一档；优化位2026预算57.11仍低于最便宜58.34，所以无成交。原位2026有11512个报价按初始权益可负担。后续必须检查lot可行性，不将不交易判为稳定。
- position_weight_decomposition.csv将真实PnL分解为平均等名义金额恒等项和仓位/单位收益协方差项。只是诊断，不是重新定仓回测；加仓和风险曲线/资金路径混合影响，不能凭此单独认定某曲线有因果问题。

## 下一轮框架已准备但尚未原生验证

- build_next.py已生成next_packages下独立8位置、next_factory/next_run/next_experiments。只修改新副本，不改第一轮冻结85文件或此前1517文件；两份哈希再次核对未变。
- next MR L支持max_units1..4（原归档固定4），按实际层数重算风险；time24/time48退出可等待可见5分钟BOLL中轨重新入场。默认功能关闭；趋势/PBS下轮沿用已有配置字段。详见NEXT_CYCLE_PROTOCOL.md。
- test_next_allocation.py最终11 passed（1/2/3/4层风险覆盖及向下取整、四层参数等价、重入条件、最小数量可行性）；初次跨包dataclass直接相等断言因类型不同失败，已改asdict比较，非运行策略故障。
- evaluate_followups.py支持crosscheck（需round1_shortlist）、next_smoke（11项）、next_develop（46项）。这些阶段尚未启动，必须等当前session26223结束，且先对next_smoke做原生默认等价/分层数量/退出门槛核验后才能跑开发。还需编写该next_smoke核验与后续汇总。
- 第一轮原位MRL7项全齐：main -113.66/41.60%；time24 -245.50/56.44%，time48 -308.52/68.16%，time72 -310.91/67.35%，band4 -173.41/40.21%，band8 -258.52/59.86%，flat -45.35/32.70%。保持原筛选规则，未读全38项前不运行完整report或宣称赢家。
- 上一个目标轮为实质进展；本轮又新增130项入场背景诊断、lot必要预算证明、权重分解、下一轮8包及11测试。整体目标仍进行中，不能complete。

- verify_next_smoke.py已编写并Ruff通过（未执行）：11项默认等价、真实1/2层最大数量、time24触发/时限、账目步长及旧文件不变；通过后才写frozen_next_runtime.json并允许next_develop。
- 最终下一轮行为测试扩充为11 passed，新增验证：同风险预算下四层不足最小一档而一层可买0.002 BTC，仍不向上取整越预算。

- 本轮增加crosscheck_report.py（尚未运行）：跨期全部数值、单位名义盈亏/活跃性和曲线。continue_after_round1.py可在26223确认终止且ALL_COMPLETE后顺序执行round1_report→next_smoke→verify_next_smoke→crosscheck→crosscheck_report→next_develop；尚未启动。
- 在所有clock候选尚无metrics时修订频率门槛：相对最低交易数对clock15/30乘5/信号分钟，绝对最低15开发/10复核不变。理由及时间记录在ROUND1_SELECTION.md，避免把正常低频误判为停交易，非按clock收益改门槛。

- 本次继续已通过session26223确认第一轮仍运行，不能按进程列表隔离视图判停止；最新日志TFL lock50完成、lock80及TFS main进行中。TFL开发期cost5净亏24.09/DD12.30%，main净亏35.58/DD12.60%；clock30少亏但DD变大，lock50盈利8.38但DD14.96%。未据此提前接受。
- 新增next_report.py并Ruff/编译通过，第二轮结果产生前声明最多两个不同变更类别候选的规则，写入NEXT_CYCLE_PROTOCOL.md。尚未执行，因为第二轮原生回测未开始；需第二轮完成后执行该汇总，再安排跨期。此前1517文件和第一轮85冻结文件再次全部未变。整体优化目标未完成，继续原定阶段，不将较早BTC尺度报告当成本轮交付。

- 本轮为实质进展及多次50秒验证等待：session26223仍live，最新完成TFS clock15并启动cost3，clock30仍计算。新增evaluate_followups next_crosscheck，crosscheck_report --next（独立next_base/next冻结源码/独立输出），continue_after_round1扩充next_report→next_crosscheck→next_crosscheck_report。Ruff通过、报告入口help通过；全流程未启动，仍必须等26223确认退出0且round1 ALL_COMPLETE。
- 新读开发结果TFL lock80=-72.41/DD17.35%；TFS main=+3.3318/DD3.8372%/48笔，time24=-17.351/DD4.656%/54笔，time48=+0.186/DD3.380%/50笔。单纯统一缩短持仓不通过，仍不提前挑选赢家。目标未完成；下一步继续等待当前38项，然后启动已经准备好的串行后续流程。

- 本轮属于验证等待并新增成交证据：26223经多次50秒poll仍live，最新31/38项完成，PBS main已启动、TFS lock80仍计算。TFS clock15=-1.0212/DD1.5454%/13笔，clock30=-14.9352/DD3.2191%/9笔，均不达开发最低15笔。TFS cost3/cost5与main关键成交字段逐项完全相同，已保存tfs_cost_filter_evidence.json，故不能把这两项描述为有效改善。下一步继续等待26223退出0及38项ALL_COMPLETE，再启动continue_after_round1；当前未启动后续串行阶段。

## 第一轮完成，后续流程已运行

- session26223已确认exit0，round1.log ALL_COMPLETE round1 38。round1_report已成功执行，6项候选：两MRL flat、TFL cost5、TFS lock80、PBS reward2和falling_ma。开发通过不是最终接受。
- 第一次post流程session91398在next_smoke终止exit1：新TFL space_budget1.5被旧的名义上限<=1验证拒绝。已只修复next_packages/tfl_original和tfl_optimized校验为有限正数（同TFS），同步build_next；风险定仓/真实保证金约束未改。受影响TFL next_base原结果及失败日志归档archived_before_tfl_cap_fix，新参数尚未冻结时重跑受影响范围，其余缓存按哈希重用。
- 后续流程现session12989 live：next_smoke11项全部完成且verify_next_smoke通过，frozen_next_runtime.json已生成；已开始crosscheck22项，接着自动crosscheck_report→next_develop46项→next_report→next_crosscheck→next_crosscheck_report。不要另起原生批次或修改冻结代码。
- 全部8位置下轮实验配置已逐项实例化通过（补查了确切space_cap2.0名称；先前手动探针space_cap2拼错，不是实验配置错误）。1517旧文件、85第一轮冻结文件及下一轮冻结清单均未变。目标未完成，仍需跨期/第二轮/邻域/全历史与最终8副本交付。

- 本轮session12989多次50秒poll确认仍live，crosscheck已完成两MRL2024、MRL原位近期及TFL2024，继续运行。新增plot_round1.py及round1_curves.png，全部38开发候选，黑线基线、彩色入选、灰色其余；已目视检查并缩减日期刻度重叠，ROUND1_RESULTS.md附图。日末回撤图不冒充分钟最大回撤。PBS reward2曲线前段累积利润后持续回吐，虽期末相对改善仍不能认定稳定曲线。此前目标轮为实质进展（第一轮完成、next_smoke修复和核验、跨期启动），本轮为新增曲线证据及验证等待。继续当前session，不重启冻结运行。

- 本轮为验证等待：session12989连续三次50秒poll确认live，crosscheck已完成10项基线，开始MRL原位flat的validation/review；共22项，不另起或重启。独立起始TFL2024=-8.83/DD7.23%，近期=-31.89/DD9.74%；TFS2024=+14.27/DD2.79%，近期=+6.69/DD2.40%。候选尚未齐备，不能提前判跨期改善。后续脚本仍自动串行运行，目标未完成。

- 新跨期证据：MRL原位flat2024净亏28.70/DD18.21% vs基线净亏23.52/DD15.83%，未延续开发改善。近期flat+36.42/DD5.14% vs基线-159.98/DD41.99%，但成交审计显示首次成交2026-02-12、末次2026-08-20，仅5活跃月且2025完全无成交，不能把近期平坦判稳定。MRL优化位flat2024=-30.47/DD16.15% vs基线-4.64/DD12.41%。跨期报告新增first_fill/last_fill/active_years字段（仅报告，不改冻结运行）。session12989三次50秒poll仍live，TFL2024cost5完成、近期cost5和MRL优化位近期flat仍运行；最终结果以日志为准。

## 跨期阶段完成，第二轮已启动

- 本轮实质进展及验证等待：session12989仍live，crosscheck22项全部完成，crosscheck_report成功并已目视核对资金曲线。6候选全部mixed_or_failed：均未在2024与近期两个区间同时收益提高且回撤不增，不接受为最终优化版。
- TFL cost5：2024 -9.88/DD7.60% vs基线-8.83/DD7.23%，近期-28.70/DD9.16% vs-31.89/DD9.74%。TFS lock80：2024+9.92/DD2.01% vs+14.27/DD2.79%，近期+8.40/DD2.49% vs+6.69/DD2.40%，属于取舍。PBS reward2：2024-45.50/DD9.82% vs-36.67/DD10.41%，近期+44.81/DD9.40%，仍跨期不一致。
- 自动流程已启动next_develop46项，最新MRL_original next_base/space_units1_same_risk运行。第二轮源码已冻结85文件，不可修改。后续自动next_report→next_crosscheck→next_crosscheck_report；仍需结果后的总诊断、邻域或组合、全历史及8个最终独立副本，目标尚未完成。

- 本轮实质进展及验证等待：session12989仍live，next_develop首两项完成。MRL原位同预算一层=+4.7755/DD31.6133%/681笔/费用60.7896 vs四层基线-113.6637/DD41.5981%/923笔/费用69.1073；成交后峰值数量0.024→0.033 BTC，峰值名义/权益1.1407→1.4688，不能称单纯缩仓。完整数据first_layer_allocation_evidence.json。尚未跨期。
- crosscheck_report.trade_shape新增成交后实际峰值数量/名义/杠杆及非正权益计数，next_report自动复用；仅报告文件变动。短期TFL预算1.5持仓0.004→0.007 BTC，TFS账户杠杆5→10仍0.004 BTC且暴露同；默认账户杠杆与实际暴露区分已验证。stress路径需要stream子目录，手动读取已纠正。next_develop当前MRL原位space_units1_same_unit和space_flat_units1运行，后续阶段自动衔接，禁止改冻结交易源码。

- 本轮多次50秒确认session12989仍live；next_develop已完成MRL原位首4项。space_units1_same_unit净亏21.744/DD10.9024%/564笔/胜98.0496%/盈亏比0.0169/费用13.4074，额外5bp后-52.824；space_flat_units1净亏146.4212/DD48.6096%/686笔/胜98.2507%/盈亏比0.0138/费用56.5897，额外5bp后-281.469。降低预算少亏不代表正期望；flat和一层叠加未改善。两层same_risk/same_unit仍计算，所有第二轮结果齐备后按既定规则筛选，不提前改变格点。目标仍未完成。

- 本轮验证等待产生新证据：session12989三次50秒poll保持live；MRL原位space_units2_same_risk开发净收益86.6288/DD28.6025%/806笔/胜98.3871%/盈亏比0.0192/费用70.8286；额外每侧5bp固定成交路径扣费后-70.9595，不能把开发盈利视为稳健。space_units2_same_unit净亏34.683/DD20.7989%/689笔/费用30.1735，额外5bp-101.3967。两层同总预算比一层和四层开发表现更好，但需后续跨期及成本验证。当前next_develop已完成6/46，正在space_flat_units2和next_time24_rearm；按预定规则等全部完成后统一筛选，不改冻结代码。

- 本轮三次50秒确认session12989仍live。MRL原位space_flat_units2完成：-109.3026/DD40.6765%/896笔/费用63.6325，弱于两层保留原风险曲线的+86.6288/DD28.6025%。next_time24_rearm已走到engine.end，next_time48_rearm运行；以实际metrics/主日志终态为准，不凭engine.end宣称完成。next_develop至少7/46完成，后续串行流程不变，目标未完成。

- 追加：next_time24_rearm最终metrics已产出，净亏245.5018/DD56.4404%/1816笔/费用124.7592，与第一轮time24关键成交(ts/side/qty/px/fees)逐项相同。parameters确认cycle_rearm_after_exit=true，源码确实设置/检查rearm，BOLL索引1确为中轨；目前只证明该条件未改变实际成交，不宣称实现缺陷或有效改善。后续可按具体退出时刻审计等待是否早于原来准入限制恢复，不要改冻结代码。

- 本轮验证等待与新证据：12989三次50秒仍live，next_develop10/46完成，MRL优化位next_base已启动，原位margin10仍运行。next_time48_rearm=-314.7076/DD68.9746%/1491笔 vs不等待time48=-308.5212/DD68.1640%/1519笔；等待确有改变成交但效果更差（24小时方案成交相同不能据此认定实现未生效）。原位账户杠杆2倍结果-113.6637，关键成交与5倍next_base完全一致，确认此区间下调账户保证金杠杆并未改变实际交易。后续阶段继续自动运行，目标未完成。

- 本轮验证等待加成交证据：session12989三次50秒确认live。MRL原位账户杠杆2/5/10的关键成交、净收益-113.66370346、回撤41.59811017%全部一致；保存mrl_account_leverage_evidence.json。原位11项已完成，MRL优化位next_base也完成，next_develop12/46；优化位space_units1_same_risk和same_unit继续计算。不要把仅改账户杠杆称为改善。全部后续阶段仍同一session自动衔接，目标未完成。

- 本轮session12989三次50秒poll确认live，next_develop14/46完成。MRL优化位同预算一层净收益30.1456/DD30.4587%/615笔/费用53.4622，额外5bp后-99.5266；基线-117.0946/DD40.1933%/768笔。方向与MRL原位相符，但高回撤及成本敏感仍在，不能提前接受。优化位same_unit一层也已完成，flat一层与same_risk两层运行，完整指标见metrics。继续既定流程，不改冻结代码。

- 本轮三次50秒验证session12989仍live，next_develop16/46完成。优化位flat一层-135.324/DD46.644%/619笔/费用50.6513，未改善。优化位同预算两层完整结果 {"net_pnl": 60.9332, "max_drawdown_pct": 28.8946, "cashflow_trades": 727, "fees": 60.6028, "extra5bp": -75.3938}；仍待跨期与成本评估。优化位same_unit两层、flat两层正在计算；不改变筛选规则或运行参数，目标未完成。

- 本轮session12989三次50秒验证live，next_develop18/46完成。优化位两层same_unit=-48.2796/DD23.4895%/621笔/费用27.4181；优化位flat两层={"net_pnl": -81.3546, "max_drawdown_pct": 37.0723, "cashflow_trades": 809, "fees": 59.0654}。24/48小时rearm仍计算；前轮和本轮观察均只算开发证据，后续继续原定跨期及完整8位置交付，未完成目标。

- 本轮三次50秒验证session12989仍live，next_develop19/46完成。优化位24h恢复等待对照={"time24": {"net_pnl": -229.0314, "max_drawdown_pct": 54.794, "cashflow_trades": 1601, "fees": 119.0855}, "next_time24_rearm": {"net_pnl": -229.0314, "max_drawdown_pct": 54.794, "cashflow_trades": 1601, "fees": 119.0855}}；优化位48h恢复等待及margin2继续计算。没有重启或改冻结配置；本轮为验证等待并获取完整结果证据，后续按既定流程继续，目标未完成。

- 本轮三次50秒确认session12989仍live，next_develop21/46完成，TFL next_base已开始，优化位margin10仍计算。优化位next_time48_rearm=-318.8545/DD69.9937%/1264笔 vs无恢复等待time48=-311.1715/DD68.961%/1285笔，两组均更差。优化位margin2净收益与回撤=(-117.09462922, 40.19327588895257)；仍不提前修改预定筛选。后续继续趋势/PBS空间实验和跨期，目标未完成。

- 本轮验证等待：session12989三次50秒poll均live，MRL优化位margin10完成，均值回归22项全齐；TFL next_base完成且开发净收益-35.57506178与第一轮一致，next_develop23/46完成。TFL next_slope_level已推进2023-09，space_budget0.5正在运行。未提前筛选或修改冻结文件，后续原定next_report/next_crosscheck自动执行，目标未完成。

- 本轮三次50秒验证session12989仍live，next_develop26/46完成。TFL next_slope_level=-54.0411/DD14.9436%/454笔（基线-35.5751/DD12.60%），未改善；space_budget0.5=-11.9143/DD5.9539%/505笔/费用54.3326，少亏但仅风险调整，不能声称机制正期望。space_budget1.5={"net_pnl": -63.4612, "max_drawdown_pct": 19.6173, "cashflow_trades": 518, "fees": 163.3162}。TFL cap0.5/cap2.0仍运行，后续仍按预定流程跨期筛选，不改冻结配置，目标未完成。

- 本轮session12989三次50秒确认live，next_develop29/46完成。TFL cap0.5=-30.8872/DD11.2283%/518笔/费用88.0569，cap2.0净收益-40.6165，均仍亏；margin2=-35.57506178与基线收益相同。TFL margin10仍计算，TFS next_base已启动。后续继续趋势空头/PBS以及自动跨期，目标未完成，未改冻结源码。

- 本轮为验证等待：session12989三次50秒poll确认live，next_develop31/46完成，TFL八项全部完成（margin10净收益-35.57506178与基线相同），TFS next_base=+3.33180676已完成；TFS next_slope_level与space_budget0.5运行。无故障，不重启批次，不改参数；后续自动报告及跨期仍待全部46项完成，目标未完成。

- 本轮三次50秒确认12989仍live，next_develop34/46完成。TFS next_slope_level=+16.4197/DD2.3607%/35笔/胜54.2857%/盈亏比1.2319/费用5.05，额外5bp后+11.3698；优于开发基线+3.3318/DD3.8372%/48笔但仍需跨期（不是盲OOS）。TFS预算0.5=-1.6409/DD1.8701%/42笔；预算1.5={"net_pnl": -1.3178, "max_drawdown_pct": 6.2904, "cashflow_trades": 48, "fees": 10.9485}；cap0.5/cap2.0仍运行。未提前接受或改筛选规则，目标未完成。

- 本轮session12989三次50秒确认live，next_develop37/46完成，PBS next_base已启动，TFS margin10仍运行。TFS cap0.5=+4.2132/DD3.7528%/48笔/费用6.3057，微幅开发改善；cap2.0净收益+3.33180676与基线相同。仍须全部完成后的自动跨期，不把微小差异当参数稳定。目标未完成，冻结源码未改。

- 本轮三次50秒确认12989仍live，next_develop40/46完成。TFS八项全齐；PBS next_base=-114.36974662与前轮一致，PBS风险0.005={"net_pnl": -32.7059, "max_drawdown_pct": 10.8467, "cashflow_trades": 82, "fees": 13.3118}。PBS风险0.01/0.02仍计算；后续名义上限/账户杠杆及自动报告跨期尚待完成，目标未完成。

## 第二轮开发完成，第二轮跨期已启动

- 本轮实质进展：next_develop46项全部完成，next_report完整核验/汇总通过；session12989仍live，已自动启动next_crosscheck26项（10基线+8候选各两时间区间）。候选为两MRL各space_units2_same_risk和space_units1_same_unit；TFL space_budget0.5；TFS next_slope_level及space_cap0.5；PBS space_risk0.005。全部development_only且mechanism_claim=false，降低风险不冒称机制成功。
- 当前正在MRL原位validation/review next_base。此session后续自动next_crosscheck_report，然后结束；结束后仍需跨期结果后再次总诊断、有限组合/邻域、完整2021回测与最终8独立副本验收，不能把流水线结束当目标完成。
- 此前1517旧文件和两轮各85冻结文件再次全部哈希一致。PBS风险0.01=-76.3216/DD21.9184%，风险0.02=-141.4044/DD38.3563%，cap1=-74.0092/DD23.0987%，均未解决亏损机制。新开发完整结果NEXT_RESULTS.md/next_comparison.csv，候选next_shortlist.json。

- 本轮实质进展及验证等待：plot_round1.py新增--next，已生成next_curves.png并目视检查，NEXT_RESULTS.md附图。完整46候选含灰色未入选，日末回撤明确区别分钟峰值。两MRL同预算两层仍有2022显著回吐、之后增长慢，不能将期末盈利解释为平稳曲线；TFS方向过滤有改善但35笔样本少。session12989两次50秒确认live，next_crosscheck已完成3/26基线，MRL优化位近期/TFL2024基线在计算。后续自动next_crosscheck_report；目标未完成。

- 本轮三次50秒验证12989仍live，next_crosscheck9/26完成，PBS近期基线及MRL原位两层同预算2024候选运行。已对当时完成的7/10新基线与第一轮同区间逐关键成交和PnL/DD/费用/笔数核对全部一致，next_baseline_partial_parity.json；余下3项在完成后可补齐，不把部分核对说成全部。候选尚无完整跨期结论，继续原流程，目标未完成。

- 本轮三次50秒确认12989仍live，next_crosscheck13/26完成。MRL原位同预算两层2024=+11.5682/DD19.6731%/200笔/费用17.0772（基线-23.5237/DD15.8277%，收益好但DD差）；额外5bp后-27.4939。近期两层=-143.9932/DD40.8122%/321笔，开发盈利未延续。原位低预算一层2024=+1.3877/DD4.7386%/98笔；近期仍计算，需检查活跃性。下一轮可检验已存在space_units2_same_unit(0.15总预算)的跨期风险邻域，当前shortlist因类别上限未包含它；不要修改当前预选，等26项及报告完整后再按证据明确声明追加，而不是临时挑区间。优化位两层2024也在运行。目标未完成。

- 本轮三次50秒确认12989仍live，next_crosscheck18/26完成。原位低预算一层近期-25.5366/DD9.803%/212笔/17活跃月，首成交2025-01-01、末2026-08-19，非停止交易。优化位两层同预算2024+57.4322/DD8.2887%/166笔/8活跃月，近期-131.6071/DD38.5108%/310笔；开发与2024改善但近期仍大亏，不能称持续盈利。优化位低预算一层2024+4.0764/DD4.6637%/93笔，近期仍计算。TFL预算0.5已完成2024，近期刚启动；后续需完整连续回放及邻域，目标未完成。

## 第二轮跨期完成，第三轮已启动

- session12989已确认exit0，POST_ROUND1_COMPLETE；第二轮跨期26项及报告完成。4项both_improved：两MRL低预算一层、MRL优化位同预算两层、PBS风险0.005。其他mixed_or_failed，风险调整不当机制优势。TFS方向过滤2024+19.024/DD2.3174%/14笔但近期-1.6374/DD2.229%/32笔，额外5bp后-6.2813；TFL预算0.5在2024-10.4621/DD4.3121%，近期-15.2114/DD4.5537%，仍取舍。
- 已写ROUND3_PROTOCOL.md：固定6组合（TFL预算0.5+cost3/5，TFS方向+lock80，PBS风险0.005+reward1.5/2/2.5）各develop/validation/review18项，加两MRL已有space_units2_same_unit各validation/review4项，共22；组合先六项stress核验。不是盲测，不按单项结果扩大网格。
- 新建third_experiments.json（6独特组合、6原/优化位置共12配置）、third_run.py（逐字反向比较仅实验文件名区别next_run，策略代码不变）、third_evaluate.py。所有12配置实例化通过，Ruff/格式通过。frozen_third_runtime.json记录下一轮85文件+第三入口/第三实验/下一轮实验。
- 当前新进程session33504 live，third_evaluate.log。第三轮六项smoke已全部完成、账目/价格步长/源哈希核验通过，已开始third_cases22项，最新TFLcost3半预算develop/validation运行。不要重启旧12989或另开原生批次。
- 后续待编写第三轮完整报告（混合third_run和next_run，参照对应frozen runner和参数）；基线使用已完成next_base同区间，组合另外与单独风险/单独机制对照，不误归因。仍须有限邻域后的全2021连续回放、八个最终独立副本及完整审计，目标未完成。

- 本轮实质进展及验证等待：session33504两次50秒确认live。新增third_report.py，Ruff/格式/编译通过，尚未执行，必须等third_cases22项全部完成。报告补入两MRL已完成开发结果形成24行，分别对比next_base和单独组成部分（MRL两层0.3、TFL预算0.5、TFS单独方向、PBS风险0.005），核验时间/runner/源码/账目，输出third_comparison/third_decisions/THIRD_RESULTS。
- TFL cost3+半预算已三时期全齐：开发-9.8129/DD5.9539% vs单独半预算-11.9143；2024 -10.4621/DD4.3121%，近期-15.2114/DD4.5537%，后两区间与单独半预算同指标，不能当跨期新增过滤有效。第三轮约4/22完成（以主日志为准），cost5半预算develop/review仍计算。目标未完成，不提前接受。

- 本轮三次50秒确认session33504仍live，third_cases10/22完成。TFL cost5半预算develop=-4.9533/DD5.9539%，2024=-11.284/DD4.4756%，近期=-13.2549/DD4.2228%，2024相对单独半预算也差，未稳定优越。TFS方向+lock80：develop+18.9723/DD2.2137%/35笔，2024+14.2191/DD1.6154%/15笔，近期+0.6588/DD1.9967%/32笔；三个独立区间盈利且DD低但近期收益仍低于原基线+6.69，必须连续回放看取舍，不直接拼接。PBS reward1.5低风险2024完成、开发/近期运行。目标未完成。

- 本轮三次50秒確認33504仍live，third_cases16/22完成。PBS低风险+reward1.5：开发-6.8117/DD7.3149%/84笔，2024-17.8541/DD3.6208%/21笔，近期+7.7715/DD2.1557%/47笔。reward2：开发-5.6191/DD8.2224%/82笔，2024-15.7971/DD3.2096%/21笔，近期+20.988/DD2.3109%/47笔；相对原基线各期改善但仍不称持续盈利。reward2.5已完成2024，开发/近期仍计算。全三邻域结果后再选连续全历史，不直接拼接独立账户数字。目标未完成。

## 第三轮完成；固定时间邻域补测运行中

- session33504确认exit0，THIRD_COMPLETE；third_report由session38522执行完成exit0，24项完整对照已输出。PBS三reward邻域、两MRL两层0.15预算在三个区间均相对原基线收益更高且DD不高；不等于都盈利，且均不在每期优于其单独组成部分。
- PBS reward2.5：开发-2.6985/DD8.6183%，2024-13.7401/DD2.8577%，近期+19.5126/DD3.2038%，与reward2各有取舍。MRL原位两层0.15在2024+17.4233/DD6.1018%，优化位两层0.15在2024-1.53/DD9.40%，近期-69.80/DD22.58%，严格保留失败收益。
- 因TFL前三轮仍无一致机制改善，按第一轮clock30少亏但DD高+第二轮半预算降DD的证据，声明FOURTH_PROTOCOL.md：固定20/30/40分钟信号+半预算，小时ATR及原5m训练系数冻结（分布含义改变，不称同统计覆盖），不无限扩大网格；结束后推进全历史/八位置交付。
- 新建fourth_experiments.json、fourth_run.py（只换实验文件名）、fourth_evaluate.py（从第三轮流程改三smoke+九正式，无MRL附加）。当前session93266 live，fourth_evaluate.log；三smoke全部完成且账目/源码/价格核验通过，nine正式已完成20m2024，20m开发/近期运行。冻结next及此前代码不变。
- 第四轮结束后需完整报告（基线next_base+单独半预算+20/30/40全邻域），并进入有限全历史候选回放，尚未编写第四报告/全历史流程或最终八新包。不得把第三轮完成等同目标完成。

## 时间邻域完成，连续全历史已启动

- session93266已确认exit0，FOURTH_COMPLETE；fourth_report.py编写并执行成功（session24676 exit0），完整9行对照FOURTH_RESULTS.md。30m半预算开发-9.7415/DD7.0646%，2024+0.728/DD1.7784%，近期-24.3176/DD5.6704%，各段优于原基线。40m开发+8.09/DD2.62%，2024-2.59/DD1.28%，近期-17.44/DD4.10%，也各段改善，不隐去邻域。
- 已写FULL_CYCLE_PROTOCOL.md和full_cycle_evaluate.py，固定17主项（五next_base基线，两MRL各一层低预算/两层低预算/两层同预算六项，TFL cost5半预算/clock30半预算，TFS cap0.5/方向lock80，PBS低风险reward2/2.5）。当前新session33535 live，full_cycle_evaluate.log，已开始两个MRL full next_base。frozen_full_cycle.json合并旧1517和所有研究冻结清单并核验，不改运行代码。
- 在任何全历史候选结果出现/读取之前，补充40m时间邻域1项full_cycle_neighbor_cases.json，理由及时间记录在FULL_CYCLE_PROTOCOL附录；不改正在运行的17主批次。必须等session33535终止exit0后，另运行 `.venv/bin/python -m strategy_python._optimization.btc_cycle_20260915.fourth_run TFL_original full --variant fourth_clock40_budget_half`，然后完整全历史报告须包含18项，不能忘记该邻域。
- 后续尚待：全历史报告/图/年度/成本/活跃性与基线核验，基于完整池确定八位置的独立新包（含真实默认配置）并验证副本等价及旧文件未变。当前只完成研究候选，不是最终8交付；整体目标未完成。

- 本轮实质进展：新增full_cycle_report.py（等17+1共18项完成后执行），核验冻结代码/时间/账目与年度期末权益，输出全池CSV/逐年CSV/全曲线/报告，Ruff及编译通过但未运行。必须先补跑40m邻域后再汇总，不能漏第18项。
- 新增build_final.py，需未来final_selection.json的8项[{slot,source_slot,variant,module,reason}]，必须已生成完整全历史CSV且每个选择full净收益高于对应next_base、回撤不更高。复制已冻结next源包至strategy_python/btc_cycle_optimized_20260915_<slot>，固化Config各字段默认值、保存profile/参数差异、create_config/create_strategy入口；不覆盖已有目录、不改原策略。尚未执行，不是最终新包已完成。
- 参数固化函数已用5独特家族的真实开发回测配置执行验证（session85283 exit0）：每个生成Config的完整JSON与回测参数逐字段完全一致，含MRL嵌套元组。原生副本成交核验仍待最终选定后完成。
- 当前全历史主进程session33535仍live（50秒及5秒poll确认），MRL原位full next_base刚产出-170.42417072，与此前基线一致；两层等候选尚未查看完整结果。主批次17项自动继续，之后必须补40m第18项。目标未完成，后续实际8包及验证尚待。

- 本轮实质进展及验证等待：新增prepare_final_validation.py（仅在八包已物化后执行，生成final_runtime/final_strategies/final_experiments/final_factory/final_run，数据写final_runs隔离）、final_evaluate.py（八实际新包各独立full2021原生回放，逐关键成交、每日权益、配置JSON和指标与被选研究full版本精确对照，冻结新旧源码并输出FINAL_VERIFICATION）。Ruff/格式/编译通过，尚未执行。没有用仅导入测试代替实际新包全历史验收。
- session33535两次50秒确认live，主full17已完成4基线（两MRL/TFL/TFS），PBS基线及MRL原位一层低预算候选运行。TFL full next_base=-72.56899363与此前基线一致。仍必须主批次结束后补40m第18项，再运行full_cycle_report，选定8项写final_selection后build_final→prepare_final_validation→final_evaluate；然后最终报告/逐项审计。目标未完成。

- 用户重申用2021年开始跑；已确认当前full连续区间2021-01-01至2026-08-30，session33535仍live，5基线及MRL原位单层低预算共6/17主项完成，两层低预算/同预算在跑。MRL原位单层低预算full=-47.16543386，收益率-9.4331%，DD14.7069%，826笔；相对原基线少亏、风险下降，但仍亏损，不称正期望。
- 新增final_report.py并通过Ruff修复检查/编译；必须FINAL_VERIFICATION八实际包完整核验后才执行，输出16行前后比较、逐年CSV、8幅日末PnL图及选择理由/参数入口/限制说明。尚未执行，尚无最终八包。后续仍为33535结束→40m邻域第18项→full_cycle_report→选择八项→build_final→prepare_final_validation→final_evaluate→final_report及目视/旧源审计。目标未完成。

- 本轮两次50秒poll确认33535 live，主full进展8/17，两个MRL优化位低预算候选计算。已复核1517原文件哈希全未变。MRL原位四full方案已齐，mrl_original_full_tradeoffs.json保存收益/年收益/成本/活跃月/实际成交杠杆。两层同预算full=-21.8523/DD43.6230%，较单层低预算full=-47.1654/DD14.7069%收益好但DD显著大；2025=-72.4271、2026=-50.5067；额外5bp后-307.5754。开发期盈利未延续全历史，不应单凭期末收益选择。单层低预算54活跃月/末成交2026-08-19，两层低预算53月，少亏非停止交易。后续主批次结束及40m邻域/最终八包验收仍待，目标未完成。

- 本轮33535经50秒poll确认live，主full已9/17。新增complete_baseline_parity.json：5独特基线×2024/近期/full共15项逐关键成交、每日权益全精确一致，关键指标差<1e-7；full对照旧mechanism_full记录，其余对照第一轮，审计进程12324 exit0。补齐此前仅7/10的部分核对。MRL优化位单层低预算full已完成，其余两层候选运行，未启动新原生批次。目标未完成。

- 本轮两次50秒poll及5秒确认33535 live，主批次10/17完成，TFL费用5半预算full已启动，MRL优化位两层同预算仍运行。新增CYCLE_RESEARCH.md，按总子时空串联四轮假设/全结果/失败/空间与时间解释边界，所有现有证据链接已核验；final_report.py加入入口，Ruff通过。该研究过程不是最终八包验收，仍需完整池和副本回放。目标未完成。

- 本轮两次50秒poll确认session33535 live，主full11/17完成，两MRL各3候选全部齐。优化位两层同预算full=-0.99974054（-0.19995%）/DD39.22843%/1225笔；2025=-78.32198、2026=-30.48000；额外5bp后-250.58074。不能用期末接近零掩盖39%回撤和近两年亏损。当前TFL费用5半预算及30分钟半预算两full运行，禁止另开原生批次；仍待主结束后40m邻域及18全报告/八新包验收。已只读检查prepare_final_validation和execute的独立默认参数/输出路径/最多2引擎流程。目标未完成。

- 本轮两次50秒poll确认33535 live，主full12/17完成。TFL第三轮费用5+半预算full=-28.43490474（-5.68698%）/DD8.933865%/875笔/费用94.44106；原基线-72.56899/DD19.10015%/949笔/费用206.93235。额外5bp后-122.87597；仍亏损，不将预算缩减全部归功于过滤。当前TFL30m半预算及TFS cap0.5 full运行，之后仍需40m邻域和最终新包验收。目标未完成，本轮为经句柄确认的有效等待并新增完整TFL证据。

- 同轮第三次50秒poll后主full13/17，TFL30m半预算full完成：{'net_pnl': -31.223144509999997, 'return_pct': -6.244628902000004, 'max_drawdown_pct': 11.710920307373273, 'cashflow_trades': 262}。两个TFS候选运行。

- 本轮两次50秒poll确认33535 live，主full14/17完成，TFS cap0.5完成、TFS方向lock80和PBS低风险reward2运行。尚无主批次终止，不另开40m。目标未完成，本轮为验证等待及TFS新增结果。

- 本轮两次50秒poll确认33535 live，主full15/17完成，最后两个PBS reward2/2.5低风险full运行。TFS方向lock80 full=+32.52907929（6.50582%）/DD2.21368%/82笔/胜率51.2195%/盈亏比1.3082/费用12.03185。额外5bp+20.49723、10bp+8.46538；去最佳5笔仍+8.97023；34活跃月/6活跃年，2026=-7.02956，仍有近期劣于基线及样本少限制。相对cap0.5+23.38643/DD3.75276，整体曲线指标更好，但未最终选择。仍待33535 terminal后40m第18项，完整池汇总和八新包原生验收。目标未完成。

## 全历史17主项完成，40分钟补测已运行

- session33535已明确exit0，日志FULL_CYCLE_COMPLETE 17。随后按已声明附录启动fourth_run TFL_original full fourth_clock40_budget_half，当前新session98111，日志TFL_original_full_fourth_clock40_budget_half.log。不要重启33535；等98111 exit0后执行full_cycle_report。
- PBS低风险reward2 full=-1.74569795（-0.34914%）/DD11.19671%/150笔；reward2.5 full=+3.67341618（+0.73468%）/DD10.90661%/150笔。reward2.5额外2bp即-5.99389、5bp-20.49484，盈利脆弱；两方案2022—24均亏，2025—26盈利；目标变更不能等同每年稳定盈利。
- 八最终新包尚未生成，接续：98111完成→完整18报告→选择8位置→build_final→prepare_final_validation→final_evaluate八full→final_report/图审计/完整要求审计。目标仍未完成。

## 完整18报告及八新包已生成；实际八包验收启动

- 40m补测98111 exit0，full净收益-12.00142371；full_cycle_report session83876 exit0，FULL_CYCLE_REPORT_COMPLETE18。全部源/时间/账目/年度核验通过，full_cycle_curves.png已目视检查，五家族完整池未隐藏失败。40m full收益-2.40028%/DD6.30448%优于其余TFL候选。
- final_selection.json已选8位置：MRL两位置各单层低预算（风险曲线优先，接受比两层同预算期末少赚的取舍）；TFL两位置40m半预算；TFS两位置方向lock80；PBS两位置低风险reward2.5。后三家族两位置相同参数，明确是8独立包/5独特配置，不当8独立证据。全部选择理由含风险调整、近期失败、成本及小样本限制。
- build_final session65376 exit0，8顶层btc_cycle_optimized_20260915_*新包已实际生成、全部ConfigJSON对照源full参数精确一致。prepare_final_validation exit0。final_evaluate已启动新session68453，日志final_evaluate.log，8实际新包full2021原生回放最多2并行。原33535/98111均已结束，不重启。
- 接续：确认68453终止exit0及FINAL_VERIFICATION8项→运行final_report→看final_curves→逐要求审计/旧1517哈希及新包完整/全证据→最终中文交付，目标才可complete。当前实际新包已创建但验收尚未结束，目标仍进行中。

- 本轮两次50秒poll确认final_evaluate session68453 live，目前两个MRL实际新包full运行。新增research_completion_audit.json：按8个阶段cases清单逐一核验181独特正式run路径的metrics/parameters存在、runner及全部source哈希当前一致、资金核对误差<1e-4、价格offtick=0，全部通过。该审计不替代八实际新包native验证，明确pending字段；后续最终验收完成后需更新该字段的证据引用或在最终审计中注明其历史快照状态。目标未完成。

- 本轮三次50秒poll确认68453 live；实际新包full回放已2/8完成（两MRL），TFL两个独立包运行。final_partial_mrl_verification.json已提前逐关键成交/每日权益精确比较及配置JSON核验两MRL，全通过；不能将2/8说成全8验收。最终final_evaluate会统一复核8项。目标未完成。

- 本轮三次50秒poll确认68453 live；实际新包full已4/8完成（两MRL+两TFL），两个TFS包运行。新增final_partial_tfl_verification.json：两个TFL包配置、逐关键成交、每日权益与40m研究full精确一致。最终八包统一验收和final_report仍待，目标未完成。

- 本轮四次50秒poll确认68453 live；实际八包full已6/8完成，两PBS包开始。final_partial_tfs_verification.json记录两个TFS包配置/关键成交/每日权益精确一致核对通过。最后68453 exit0后必须final_report、图像及逐要求最终审计；research_completion_audit pending字段需更新为实际FINAL_VERIFICATION引用。目标未完成。

## 最终验收与交付完成

- session68453 exit0，FINAL_NATIVE_VERIFICATION_COMPLETE8；八新包配置/关键成交/每日权益精确一致及指标/账目/冻结哈希通过。final_report session84860 exit0，FINAL_REPORT_COMPLETE8；FINAL_RESULTS.md、final_comparison.csv16行、final_annual.csv96行、final_curves.png实际生成，曲线已目视检查。
- 最终completion_audit.json复核八独立包/1517旧文件不变/每个所选净收益更高且DD更低/报告链接存在/年度覆盖。年度合计与原位基线净收益最大1.1000057e-7 USDT数值差，最初临时1e-7附加阈值触发；核实数据后沿用既有full_cycle_report的1e-4资金容差，不修改任何回测数值，新旧实际成交及每日权益仍精确匹配。
- research_completion_audit更新实际八包完成证据引用；COMPLETION_AUDIT.md按全部总子时空/新建不改旧/循环/验收/解释边界逐项核验，无剩余交付项。历史相对改善已完成，MRL/TFL仍亏、PBS成本脆弱、所有历史已参与选择，不声称无过拟合。准备标记目标完成。
