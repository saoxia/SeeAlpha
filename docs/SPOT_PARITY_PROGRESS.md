# Binance Spot 与 USD-M 永续能力对齐：实施与验收记录

本次需求：Spot 不只提供浏览器只读行情，还应有现货成交、历史补齐、持久化、
K 线、研究回测、账户、订单、撤单及风险规则。二者共享 Engine 的任务/仓位和
数据管理机制，但**不共享**金融工具、资产账本或风险规则。

## 产品身份与数据边界

| 内容 | USD-M 永续 | Binance Spot |
| --- | --- | --- |
| Product | `futures_um` | `spot` |
| InstrumentId | `BTCUSDT-PERP.BINANCE` | `BTCUSDT.BINANCE` |
| 每交易对采集日志 | `data/market_data/binance_um_1m/managed` | `data/market_data/binance_spot_1m/managed` |
| 官方日归档 | `data/futures/um/daily/trades` | `data/spot/daily/trades` |
| 资金费率 | 支持 | **不适用** |
| 杠杆 / 裸做空 / Reduce Only | 永续语义 | **禁止** |
| 回测账户 | 保证金账户 | 现金账户 |

现货官方历史成交归档 CSV 七列，2025-01-01 起部分 Binance Spot 归档时间戳
使用微秒。Rust 转换器对来源显式配置时间戳单位，保留 Tick 纳秒级事件时间；
对未确认的微秒转换条件须失败并保留原文件，不得默默降为毫秒。

## 代码入口（功能分支）

- Engine Spot 公开市场目录、K 线与最近成交（正式和测试网隔离）：`engine/connectors/binance/spot.py`、
  `engine/public_queries.py`；公开 WebSocket 使用原始逐笔 `@trade`。
- Engine 持续采集：`engine/feed/managed.py --spot-config ...` 调度独立的
  Spot 共享采集进程，不与永续资金费率连接混合。
- Engine 数据：`engine/pipeline/` 对 Spot 日归档、InstrumentId、
  生成 Bar、恢复、删除计划分别校验；`data_manager` 持久订阅和启动/停止。
- Engine 账户/订单：`/tenants/{tenant_id}/accounts/{account_id}/spot/trading/state`、
  `/spot/orders`；经原生 NautilusTrader Binance Spot 客户端执行，
  共用请求预登记、幂等和未知结果核对；需显式提交实际交易操作。
- Platform：交易页选择现货并展示可用基础/报价币种、盘口与实时价格，
  数据页选现货采集和历史；不显示永续专属的杠杆、资金费率或做空控制。
- Engine 开发回测：请求指定 `product=spot` 后读取独立 Catalog 的现货 Bar，
  使用现金账户，要求该租户已配置同产品订阅。

## 安全及验收限制

**本文件记录功能分支的实现范围，不等于实盘验证报告。**
最终验收需要 Engine、Platform 单元/集成和 Docker 跨仓库完整通过，
以及真实 Binance Spot 行情连续采集、断线恢复、官方 ZIP 校验、
币种精度、小余额、部分成交/手续费币种、订单未知结果恢复、
沙盒账户权限与长期运行的受控验证。

不能把 Spot 资产当成 USD-M 保证金；现有账户首页的 USD-M 资产汇总
没有被扩展成全市场合并净值，也不能视为包含全部现货余额和持仓成本。
策略跨现货/永续组合的同时下单、跨产品对冲和统一组合风险预算仍需单独的
组合执行验收。现货不支持借币裸卖空；若要 Margin 交易，必须另立产品类型与风控。

不得未经明确生产授权执行实盘下单，不修改真实密钥、状态卷，
不自动删除旧行情或从历史回放触发实时订单。
