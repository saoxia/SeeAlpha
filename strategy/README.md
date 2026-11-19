# SeeAlpha 演示策略（Demo）

本目录的四个包全部以 `demo_` 命名，供研究学习、回测示例和演示框架架构；**不代表策略经验证可盈利**。一个包目录打成 ZIP 时，`seealpha-package.json` 必须在 ZIP 根目录。

| 包名 | 功能 | 当前运行状态 |
| --- | --- | --- |
| [demo_ema_crossover](demo_ema_crossover/README.md) | EMA 金叉做多、死叉退出；单品种目标持仓经 Coordinator 净额执行 | **现有 Engine 单品种回测示例** |
| [demo_funding_carry](demo_funding_carry/README.md) | 等量现货多头 + USD-M 永续空头；费率、基差、仓位偏差与数据新鲜度门禁 | **研究逻辑与多腿意图；尚不可回测/下单** |
| [demo_multi_signal_portfolio](demo_multi_signal_portfolio/README.md) | EMA 趋势方向 + 布林带反向强度；单包加权目标或两独立实例由 Coordinator 统一净额 | **现有 Engine 单品种回测示例** |\n| [demo_option_hedged_bollinger](demo_option_hedged_bollinger/README.md) | 永续布林带均值回归，配合同标的看涨/看跌多头期权尾部保护与期权覆盖预算 | **研究逻辑与多腿意图；尚不可回测/下单** |

## 策略与执行边界

`prepare(parameters)` 校验并归一化策略参数。EMA 与多信号 Demo 使用已存在的 `publish(symbol, quantity)`，可直接进入 Engine 单品种 `PortfolioCoordinator`。多信号 Demo 既可在包内合成趋势/均值回归目标，也可用两个独立下发的策略实例交由 Coordinator 进行真实的跨实例净额。

另两个 Demo 将信号和多产品目标规划分离为可单元测试的 `logic.py`，并以 `publish_basket` / `subscribe_market_bundle` **明确声明对未来跨产品 Engine 的依赖**。当前 Engine 并不实现这两个接口；启动会明确拒绝，不能把多腿计划拆成互不协调的两笔或三笔订单，也不能利用现有单品种模拟回测对外宣称完整策略收益。提供的数字仅为确定性的计划输出，非真实交易结果。

尚待 Engine 支持：市场产品维度的 InstrumentId、多个交易所账户/资金及保证金建模、同步行情和期权历史快照、真实资金费结算、原子多腿目标与成交顺序/失败补偿、准确费用/滑点/到期处理、完整回测与实盘回归。**本文档和示例并未自动开启实盘。**
