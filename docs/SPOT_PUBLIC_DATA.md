# Binance 现货公开行情（阶段一）

2026-11-19：SeeAlpha 把 Binance Spot 作为独立 `spot` 市场身份，
不再与 `futures_um` 永续行情混用。同一标的 BTCUSDT 的现货与永续
对应不同 Nautilus InstrumentId、K 线类型和数据来源。

## 已开放的只读 API

由 Platform 统一验证登录身份并通过租户 Engine HTTP 契约转发，
不会在 Platform 建立交易所连接器或写入行情文件：

- `GET /api/system/exchange-markets?market_type=spot&environment=production&search=BTC`
  — Binance 已开放交易的现货市场目录；结果按搜索条件截取前 200 个。
- `GET /api/system/exchange-spot-candles?symbol=BTCUSDT&interval=1m&start_ms=...&end_ms=...&limit=1000`
  — 区间左闭右开，单次最多 1000 条；若 `next_start_ms` 非空则继续分页。
  `is_closed=false` 表示当前 K 线尚未闭合，不能用作终态历史样本。
- `GET /api/system/exchange-spot-recent-trades?symbol=BTCUSDT&limit=500`
  — 最近成交，不能视为某个时间区间内的完整 Tick 数据。

Engine 原生只读接口分别为 `/tenants/{tenant_id}/venue/catalog`（product=spot）、
`/venue/spot/klines` 和 `/venue/spot/trades`。
现货暂时只支持正式环境的**公开数据**；不需要交易所密钥，不意味着支持现货实盘。

## 明确未完成的支持

上述接口用于公开行情读取和研究探索，**不代表现货已接入数据管理面板**。
完整的现货 Tick/K 线 WebSocket 持续采集、历史归档/补洞、来源校验、Catalog、
回测使用、策略现货订单、资产持仓与精度风控仍需按 Engine/Platform 边界实现与测试。
不得把近期 REST 成交伪称历史数据或将永续账户余额当作现货账户余额。

建议后续按顺序实施：
1. Engine 持续现货 Feed + 完整来源补齐、独立存储和质量门禁。
2. Platform 现货订阅、任务/图表与策略回测选择。
3. 现货账户资产、订单及执行风控（独立于只读行情，显式授权后才启用）。
