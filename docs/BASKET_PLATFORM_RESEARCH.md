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

## 研究报告的组合腿快照（只读）

开发回测结果页展示 Engine 原生报告最后一根 K 线的各腿数量及分账户模拟权益；资金费模式的永续数量为有符号净仓位，期权保护模式当前提供的永续数量为**绝对敞口**，不应误读为持仓方向。期权模式可显示已生成目标事件数量，资金费模式可显示真实历史资金费观测数量；二者都不代表生产交易所实际订单、资金划转核对或完整收益归因。

报告字段不存在时不补造零值，也不把 K 线模拟资产视作真实账户净值。开发回测新增展示**最新一条来源可核对的策略目标事件**，包括各产品合约的有符号目标数量、适用时的策略成员数量及模型动作。该目标与最后一根 K 线的实际模拟成交仓位分别展示，不把计划伪装成成交，也不从缺损记录推断未知数据。这项只读展示不会开启实时仓位查询或策略交易。

## 保持未支持的场景

当前 Engine 不具备真实跨交易所多腿原子成交、对冲失败补偿或完整到期行权结算核算。模拟账户损益不能直接解释为真实可复制的盈利。历史未采集到的期权报价仍然缺失而非使用 K 线推断；请求会拒绝。

API 是轻量同步调用，仍需遵守 Engine 的单次历史窗口和业务超时限制。**合并 Platform 代码并不自动更新用户生产 Engine 镜像或迁移其状态**；需要显式构建并核实匹配 Engine SHA 的开发容器才能使用新入口。


## Engine 固定版本与研究边界（2026-11-19）

Engine [#28](https://github.com/saoxia/seealpha-engine/pull/28) 新增研究态本地组合阶段持久化：私有 POSIX 目录、跨进程文件锁、原子落盘、修订版并发冲突检查以及启动后的事件重放。此账本不包含实盘订单入口，也不等于真实交易订单账本；SHA-256 修订号用于校验和版本控制，并不防范拥有同 UID 文件权限的恶意代码。


Platform 已将独立 Engine 固定到完整提交
`57842187f78de46f6d75ced36ec9f13b7d658504`。
包含开发环境专用资金费/期权保护模拟、研究态可重放阶段日志（Engine #26），
带 POSIX 文件锁与修订版并发检查的持久化（Engine #28），
以及按每个执行阶段保守计算的毛名义风险预算门禁（Engine #29）。
Engine #30 新增显式的纯研究态紧急全平规划：对现有严重腿偏差或保护不足的组合，
只有全部目标归零、且各阶段保持单调减险时才允许生成规划；
本能力未接入订单出口，也不会授予生产执行权限。

**Engine 研究态增量正式固定版本：** Engine [#31](https://github.com/saoxia/seealpha-engine/pull/31) 已合并到 Engine `main`，提交 `57842187f78de46f6d75ced36ec9f13b7d658504`。本次 Platform 更新只展示经过验证的开发回测策略目标，并固定一致的独立 Engine 版本。没有开启真实订单执行；#107 C 阶段生产验收仍未完成，也不会自动更新生产 Docker。
若现有仓位已超预算，仅允许严格单调降低敞口的研究退出计划；
不能假定 Spot、USD-M 与 Options 在交易所原子成交。
该日志尚未接入全部专用模拟执行器，也不具有真实交易所订单发布权限。
通用多腿策略运行、真实成交补偿、期权到期结算及生产对账仍未完成。
锁定源码不会自动迁移或升级任何已有 Docker、实盘账户或状态卷。
