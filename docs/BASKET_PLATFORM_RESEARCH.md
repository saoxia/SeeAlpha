# 跨产品组合：Platform 开发回测入口

对应 Engine 的 [B2/B3](https://github.com/saoxia/seealpha-engine/blob/main/docs/BASKET_RESEARCH.md)，主任务 [#107](https://github.com/saoxia/SeeAlpha/issues/107)。

## 入口与权限

策略库中选择**开发租户**及其已创建的**虚拟账户**，下发具有开发授权的对应 Demo，之后使用“跨产品组合开发回测”。新增的两个只读历史回测模式：

- `funding_carry`：Spot CASH + USD-M MARGIN，Engine `/carry-backtests`
- `protected_options`：PUT/CALL CASH + USD-M MARGIN，Engine `/protected-option-backtests`；只接受到期前经验证的历史完整盘口

Platform API：
- `POST /api/tenants/{tenant_id}/accounts/{account_id}/basket-backtests`
- `GET /api/tenants/{tenant_id}/accounts/{account_id}/basket-backtests/{mode}/latest`

POST 请求必须携带 `mode`、带时区的 `start/end`、`interval=1m|5m`、`starting_primary_balance`（现货或期权）、`starting_perpetual_balance` 和 `max_gross_notional`，三笔金额均为有限正数；前两笔之和不得超过这个虚拟账户已有的初始资金。只允许 USDT 开发账户，无正式交易路径。时段及合约行情由 Engine 检查。结果明确标记为研究模拟，并保留 Engine 的原生订单、成交和权益曲线。

**授权边界**：Router 使用现有用户登录、交易权限和租户所有权检查；StrategyStore 检查开发租户和虚拟资金范围；Engine 复核已下发策略包、同账户三腿行情订阅、合约/报价时效和资金费/保护覆盖约束。Platform 不导入 NautilusTrader、不读取交易密钥、不复制执行或定价逻辑。

## 系统 Demo 的开发授权

`demo_funding_carry` 和 `demo_option_hedged_bollinger` 现在在开发场景可下发。已部署系统中，只有**原策略归档字节内容与当前仓库内的 Demo 源文件完全一致且自身哈希验证通过**，才会补发其开发授权；不会给不匹配/改写过的旧 ZIP 自动授权。系统 Demo 向所有用户可见，但只有自己的开发租户和虚拟账户能够进行研究回测。

期权策略下发必须显式指定相同标的和到期日的 `put_symbol` 与 `call_symbol`。两个 Demo 仍不暴露标准策略运行时的实盘 `publish_basket`；不允许将开发历史回测转化为真实订单。

## 保持未支持的场景

当前 Engine 不具备真实跨交易所多腿原子成交、对冲失败补偿或完整到期行权结算核算。模拟账户损益不能直接解释为真实可复制的盈利。历史未采集到的期权报价仍然缺失而非使用 K 线推断；请求会拒绝。

API 是轻量同步调用，仍需遵守 Engine 的单次历史窗口和业务超时限制。**合并 Platform 代码并不自动更新用户生产 Engine 镜像或迁移其状态**；需要显式构建并核实匹配 Engine SHA 的开发容器才能使用新入口。
