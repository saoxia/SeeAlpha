# 同标的 Spot + USD-M 显式对冲 Demo

仅限开发研究，不是套利或盈利策略，也不包含真实资金费结算。

在 SeeAlpha 的开发租户和 USDT 虚拟账户下发此 Demo，选择“通用多标的 · 原生模拟（开发）”，选择当前发行实例，市场腿按行填写：

```text
spot:ETHUSDT
futures_um:ETHUSDT
```

Demo 默认持续发布 `+0.1 ETHUSDT Spot` 与 `-0.1 ETHUSDT USD-M`，并在同一次
`publish_basket` 中声明 `constraints={"spot_perpetual_hedges": [{"symbol": "ETHUSDT"}]}`。
Engine 校验全成员合计的相同 BASE 数量，分阶段模拟：买现货并确认成交后才增加永续空头；
平仓时先减永续空头再释放现货。两腿必须有同窗口闭合 K 线和已验证的原生合约，
且共用账户的毛名义金额不能超过设置的风险预算。

同标的数量中性并非资金、基差、强平或成交风险中性。
此例不包含资金费收入、真实订单补偿、期权或实盘交易权限。
