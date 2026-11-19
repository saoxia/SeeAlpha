# Demo：多信号组合与目标仓位协调

**状态：可使用当前单品种 Engine 的已下发策略回测/预览接口；并非已经完成实盘验证。** 仅以 ETHUSDT 永续为例，不接入外部行情或私有账户接口。

## 策略意图

EMA(8,21) 给出方向信号：快线高于慢线为 +1，低于慢线为 -1，持平为 0。布林带使用最近 20 根已收盘 K 线的均值与总体标准差，给出与价格偏离方向相反的连续强度：`-((close - mean) / (deviation * std))`，再限制在 [-1,1]；标准差为零时信号为零。

按 `mode` 分别启用趋势 `trend`、均值回归 `reversion` 或两者 `combined`。每个信号乘以自身权重和 `allocation`，得到**带符号数量**而不是订单；将总量限制在 `[-risk_limit, +risk_limit]`，通过 `publish(symbol, quantity=Decimal)` 交给 Engine。正数为多头，负数为空头，零为空仓。

### 比较两条执行路径

同一组已完成预热的信号假设是：趋势 +1，回归 -1，`allocation=1` ETH，`trend_weight=0.6`，`reversion_weight=0.4`，`risk_limit=1`：

| 加载方式 | 成员 A | 成员 B | 净目标 |
| --- | --- | --- | --- |
| 单一策略包：`mode=combined` | +0.6 ETH | −0.4 ETH（包内合成） | +0.2 ETH |
| 两个独立下发实例：`mode=trend` / `mode=reversion` | +0.6 ETH | −0.4 ETH | +0.2 ETH，由 PortfolioCoordinator 进行**跨实例净额** |

**特别说明：** 第一行是一个策略内部组合信号，不会产生两个独立成员；第二行才真实使用 Engine 的跨成员净额功能。两个实例须下发到**同一开发租户、同一账户和同一 ETHUSDT 品种**，参数完全一致但 `mode` 不同，使用不同的 `issuance_id`。账户的总风险上限由 Engine `RiskClippedCoordinator` 再检查；上述示例不代表目标一定能在交易所成交。

当前 Engine 根据同一账户已加载策略的最小 `risk_limit` 约束账户的**净目标数量**，并非分别为每个策略隔离保证金，也不是约束对冲前各成员绝对敞口总和。不同账户或不同产品不会跨账户净额。请勿把净额小误解为真实的总风险或资金占用必然小。

## 参数与使用

- `mode`：`trend`、`reversion` 或 `combined`，默认 `combined`。
- `fast_period` / `slow_period`：EMA 周期（快周期必须小于慢周期），`bollinger_period`：布林窗口，`deviation`：布林标准差倍数。
- `trend_weight` / `reversion_weight`：各信号权重，非负且总和不超过 1；默认 0.6/0.4。
- `allocation`：满强度时的基础数量，`risk_limit`：单实例的最大绝对目标，都是以 ETH 为单位的**数量**，不是 USDT 资金或杠杆倍数。缺省 `risk_limit=allocation`。
- 任何模式均只在其需要的指标完成预热后发出目标。相同目标不重复发布；全部输入视为已闭合 K 线，重复或倒序的 bar 不重复计入。

可以将本目录四个文件打包成 ZIP，保证 `seealpha-package.json` 位于 ZIP 根目录，上传策略商店并发放**开发**授权。单实例选择 `combined`，或将相同包下发两次且分别指定 `mode=trend`、`mode=reversion`。该 Demo 使用 Engine 当前的 `publish` 协议；无需修改 Engine 或启用新的跨产品协议。

**限制：** 默认对目标变动逐次发布（不是每根 K 线都必然下单），频繁波动可能带来较高换手和成本；EMA/Bollinger 的参数及 0.6/0.4 权重仅是教学例子，不是盈利信号。历史回测需要足够的闭合行情、实际成交和手续费假设，不构成实盘资格。
