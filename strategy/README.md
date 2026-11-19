# SeeAlpha 演示策略（Demo）

本目录的四个包全部以 `demo_` 命名，供研究学习、回测示例和演示框架架构；**不代表策略经验证可盈利**。一个包目录打成 ZIP 时，`seealpha-package.json` 必须在 ZIP 根目录。

| 包名 | 功能 | 当前运行状态 |
| --- | --- | --- |
| [demo_ema_crossover](demo_ema_crossover/README.md) | EMA 金叉做多、死叉退出；单品种目标持仓经 Coordinator 净额执行 | **现有 Engine 单品种回测示例** |
| [demo_funding_carry](demo_funding_carry/README.md) | 等量现货多头 + USD-M 永续空头；费率、基差、仓位偏差与数据新鲜度门禁 | **研究逻辑与多腿意图；尚不可回测/下单** |
| [demo_multi_signal_portfolio](demo_multi_signal_portfolio/README.md) | EMA 趋势方向 + 布林带反向强度；单包加权目标或两独立实例由 Coordinator 统一净额 | **现有 Engine 单品种回测示例** |
| [demo_option_hedged_bollinger](demo_option_hedged_bollinger/README.md) | 永续布林带均值回归，配合同标的看涨/看跌多头期权尾部保护与期权覆盖预算 | **研究逻辑与多腿意图；尚不可回测/下单** |

## 策略与执行边界

`prepare(parameters)` 校验并归一化策略参数。EMA 与多信号 Demo 使用已存在的 `publish(symbol, quantity)`，可直接进入 Engine 单品种 `PortfolioCoordinator`。多信号 Demo 既可在包内合成趋势/均值回归目标，也可用两个独立下发的策略实例交由 Coordinator 进行真实的跨实例净额。

另两个 Demo 将信号和多产品目标规划分离为可单元测试的 `logic.py`，并以 `publish_basket` / `subscribe_market_bundle` **明确声明对未来跨产品 Engine 的依赖**。当前 Engine 并不实现这两个接口；启动会明确拒绝，不能把多腿计划拆成互不协调的两笔或三笔订单，也不能利用现有单品种模拟回测对外宣称完整策略收益。提供的数字仅为确定性的计划输出，非真实交易结果。

尚待 Engine 支持：市场产品维度的 InstrumentId、多个交易所账户/资金及保证金建模、同步行情和期权历史快照、真实资金费结算、原子多腿目标与成交顺序/失败补偿、准确费用/滑点/到期处理、完整回测与实盘回归。**本文档和示例并未自动开启实盘。**

## 策略库权限与默认可见性

Platform 启动后在业务数据库中幂等登记本目录自带的四个 Demo。
系统 Demo 对所有已登录用户可见；这不创建策略加载记录、租户或 Docker。
EMA 与多信号 Demo 自动赋予 **开发租户** 加载授权，另两个跨产品示例仅可查看和复制，
在 Engine 支持多腿执行之前不会获得开发/正式授权。

普通用户通过策略库上传的 ZIP 默认 `private`，只对本人可见。
用户可以复制可见包生成一个新的、独立的私人策略包；
新的策略标识会自动写回 ZIP 清单以满足 Engine 身份校验。
策略拥有者可将其切换为 `public`，或向指定用户名授予只读共享。
共享用户可以查看策略元数据和复制为自己的私有副本，但不能修改原包；
只有所有者或系统 Demo 可以下载原始 ZIP。公开并不授权正式交易。

每一次加载策略时，Platform 都同时验证用户对租户的所有权、对策略包的可见性、
策略版本对应环境授权和账户归属；正式环境仍需管理员的显式 `production` 授权。
策略版本、参数、加载与执行隔离在不同的租户中，不会跨账户自动生效。

管理员可通过管理后台登记系统策略包和版本；普通用户在「STRATEGIES」页面
管理自己的策略。在多实例部署之前执行 Alembic 迁移以新增归属、可见性、
共享表与组合唯一索引。旧版管理员上传的全局策略会保留为 `system`。

## 授权范围 / Licenses

本仓库核心 Platform 代码采用 Apache-2.0。四个 `demo_*` 示例包各自采用目录内的 MIT 许可证（在对应权利来源核实后发布），便于社区复制、学习和商业改造；复制或再分发时须保留相应版权及许可证说明。Demo 的公开可见性和正式运行授权是独立概念，不代表已被验证可以盈利。

用户自行上传/开发的原创策略不因使用 SeeAlpha 而自动开源。详见 [策略授权边界](../docs/STRATEGY_LICENSING.md)。
