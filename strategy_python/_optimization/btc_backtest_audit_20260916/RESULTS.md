# BTC 衍生策略：修改后回测覆盖审计（2026-09-16）

口径：2021起指交易起点2021-01-01；完整历史另要求结束2026-08-31（不含），即覆盖至2026-08-30。两者不能混称。原生记录须有参数、指标、成交和每日权益文件，策略包全部Python文件、记录中的共享源码以及回测入口哈希与当前文件匹配。归档版本不作为当前覆盖依据。

## 结论

- 直接可见的30个顶层BTC相关包：原BTC迁移8个、波动尺度版8个、最终循环优化8个，共24个当前源码均有原生全历史记录。
- 机制版5个本体保留禁止2024前交易的检查；专用回放副本有2021起完整原生结果。逐文件哈希与AST复核显示，除导入路径和明确移除起始日期限制外一致。不能说本体当前代码原样运行过2021，也不能说机制逻辑从未全测。
- 最新btc_failed_breakout_20260916已做2021起自建分钟模拟，但没有Nautilus原生全历史回测。这里只核对核心strategy.py的模拟来源哈希及已有预设复放验证记录，不能把它提升为原生证明。
- 内部第一轮packages共8个未直接连续全测；其中5个从2021开始只跑到2024年初，另3个未找到直接2021记录。
- 内部第二轮next_packages的tfl_optimized、tfs_optimized、pbs_optimized共3个草稿目录未找到直接2021记录。它们不是已经全测的顶层btc_cycle_optimized_20260915_*交付包。
- 未发现已完成的原生全历史记录因当前策略代码或入口变动而失配。另有8条过时短区间/调试记录哈希不匹配，均非完整历史记录；已列入source_exceptions.json，不据此推断最终版本缺测。

## 没有直接原生全历史记录的目录

| 目录 | 当前覆盖 |
|---|---|
| [strategy_python/_optimization/btc_cycle_20260915/next_packages/pbs_optimized](/home/lly/NautilusTrader/strategy_python/_optimization/btc_cycle_20260915/next_packages/pbs_optimized) | 未找到当前目录2021起的直接原生记录 |
| [strategy_python/_optimization/btc_cycle_20260915/next_packages/tfl_optimized](/home/lly/NautilusTrader/strategy_python/_optimization/btc_cycle_20260915/next_packages/tfl_optimized) | 未找到当前目录2021起的直接原生记录 |
| [strategy_python/_optimization/btc_cycle_20260915/next_packages/tfs_optimized](/home/lly/NautilusTrader/strategy_python/_optimization/btc_cycle_20260915/next_packages/tfs_optimized) | 未找到当前目录2021起的直接原生记录 |
| [strategy_python/_optimization/btc_cycle_20260915/packages/mrl_optimized](/home/lly/NautilusTrader/strategy_python/_optimization/btc_cycle_20260915/packages/mrl_optimized) | 当前代码跑过2021—2023，未直接连续全测 |
| [strategy_python/_optimization/btc_cycle_20260915/packages/mrl_original](/home/lly/NautilusTrader/strategy_python/_optimization/btc_cycle_20260915/packages/mrl_original) | 当前代码跑过2021—2023，未直接连续全测 |
| [strategy_python/_optimization/btc_cycle_20260915/packages/pbs_optimized](/home/lly/NautilusTrader/strategy_python/_optimization/btc_cycle_20260915/packages/pbs_optimized) | 未找到当前目录2021起的直接原生记录 |
| [strategy_python/_optimization/btc_cycle_20260915/packages/pbs_original](/home/lly/NautilusTrader/strategy_python/_optimization/btc_cycle_20260915/packages/pbs_original) | 当前代码跑过2021—2023，未直接连续全测 |
| [strategy_python/_optimization/btc_cycle_20260915/packages/tfl_optimized](/home/lly/NautilusTrader/strategy_python/_optimization/btc_cycle_20260915/packages/tfl_optimized) | 未找到当前目录2021起的直接原生记录 |
| [strategy_python/_optimization/btc_cycle_20260915/packages/tfl_original](/home/lly/NautilusTrader/strategy_python/_optimization/btc_cycle_20260915/packages/tfl_original) | 当前代码跑过2021—2023，未直接连续全测 |
| [strategy_python/_optimization/btc_cycle_20260915/packages/tfs_optimized](/home/lly/NautilusTrader/strategy_python/_optimization/btc_cycle_20260915/packages/tfs_optimized) | 未找到当前目录2021起的直接原生记录 |
| [strategy_python/_optimization/btc_cycle_20260915/packages/tfs_original](/home/lly/NautilusTrader/strategy_python/_optimization/btc_cycle_20260915/packages/tfs_original) | 当前代码跑过2021—2023，未直接连续全测 |
| [strategy_python/btc_failed_breakout_20260916](/home/lly/NautilusTrader/strategy_python/btc_failed_breakout_20260916) | 只有自建分钟模拟全测，无原生回测 |

## 参数实验不能等同独立策略代码

循环优化四轮共声明150个“位置×参数方案”（含别名草稿和重复基线）：18个有本目录的完整原生回测，75个仅有2021起的分段回测，57个未找到本目录2021起回测。此处不包含另行验收的最终八包，也不包含假跌破研究的自建模拟；不能理解为132个独立策略代码全部漏测。

某个代码包有一项全历史回测，不代表这个包的所有参数组合都完成全历史；因此另外输出逐配置清单。未通过筛选的实验方案多数只跑分段，并非最终交付遗漏。

[51个目录完整清单](packages.csv) · [12个需区分或补原生全测的目录](needs_direct_native_full.csv) · [150个实验配置覆盖](variants.csv) · [132个未连续全测配置](variants_without_full.csv) · [逐运行证据](run_evidence.json) · [旧记录失配明细](source_exceptions.json)

本次仅检查证据，不修改策略，不自动启动补测。优先缺口是最新假跌破研究候选的原生验证；内部弃用草稿是否补测应取决于是否继续保留为候选，而非为追求覆盖率全部重跑。
