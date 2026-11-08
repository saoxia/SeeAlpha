# USD-M 预估资产曲线

2026-11-07 用户明确批准：现有 Binance USD-M 账户从启用后的可信基准开始记录，
事件估值、定期对账与差异校准，以及按已保存证据一键重算。仅用于资产展示，不接入交易、
保证金或策略风控。正式与 sandbox 数据按账户及环境隔离，不包含现货、期权或开户以来历史倒推。

## 事件与金额口径

API → 引擎估值流（`engine.runtime.valuation_events` → `engine.connectors.binance.valuation`）。
账户校准沿现有 `assets.asset_account` 固定只读 GET，仍由 API 调用；标记价 LiveNode 在引擎进程。
读取完整分币种钱包及持仓；价格使用 Nautilus 公共 data-only 节点与 mark-price 订阅。
每 5 秒读取并检测账户变化，原生价格变化每 250ms 合并观察；余额/仓位或价格变化才触发估值，
没有变化时每 10 秒记录新鲜度检查点。账户变动发现有约 5 秒加网络请求时间的延迟，
不是逐个账户 WebSocket 原始事件的完整审计，不承诺捕获观察窗口内每个中间金额极值。
原操作账本、原生账户查询和交易执行路径保持独立。

不能为资产曲线新增持久执行客户端：官方说明同一账户会复用 active listenKey，
本机模拟验证原生短时查询退出会 DELETE listenKey，可能打断其他私有流。
因此本功能不创建、续期或删除 listenKey，周期对账也使用独立只读 GET，避免影响现有查询与手动交易。
公开行情节点没有执行客户端，不发送任何交易或账户配置命令。
来源：[账户信息 V2](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account)、
[User Data Streams](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/user-data-streams)。

每个事件保存完整的可重放证据：钱包各币种余额、持仓有符号数量和成本、标记价与价格时间、
资产折算价与时间，以及本地观察时间。原生对象和凭据不进入这些记录。

```text
钱包估值 = Σ(币种钱包余额 × 币种/USDT 折算价)
未实现盈亏 = Σ(有符号合约数量 × 合约乘数 × (标记价 − 开仓价) × 结算币种/USDT 折算价)
预估资产 = 钱包估值 + 未实现盈亏
```

connector 已将数量乘以原生合约乘数，API 不再次乘算。仅支持已解析的 USD-M 线性合约。
金额计算使用 Decimal，持仓成本保留交易所账户响应的十进制字符串。
手续费、资金费、已实现盈亏与划转已经反映在账户钱包状态中，不能再次累加账本的成交或收支记录。
多资产曲线按分币种指数估值，可能与交易所抵押率口径不同，不把该曲线当作可用保证金。
非 USDT 折算价为 `asset/USD index ÷ USDT/USD index`，每分钟尝试刷新，不采用小额资产一小时缓存。

账户状态超过 15 秒、缺少标记价、标记价超过 30 秒、折算价超过 120 秒、未知/无效数值或事件流中断，保存缺口，
不发布零金额。允许源时钟比本地最多提前 5 秒。断线、队列溢出、进程重启或凭据变化后，
重新启动公开行情节点并读取账户 REST 建立基准，不复用可能遗漏变化的旧状态。没有持仓的完整零余额账户可以真实估值为零。

## 校准

启动/重连时通过新的账户 REST 读取建立基准；连续运行每 300 秒通过独立只读 GET 取得余额与仓位。
两份状态按同一组已保存标记价和折算价比较，不直接拿不同时间的交易所浮盈亏作差。

- 钱包余额或持仓品种/方向/数量不一致：立即保存独立查询证据、差异和校准点，重启采集并重新建立基准。
- 其他金额差异超过 `max(1 USDT, |对账权益| × 0.05%)`：连续两次超过才校准；未超过会清除连续状态。
- 无法完整估值时，不宣称金额对账通过；结构差异仍能触发重新建立基准。
- 查询失败时保留原证据并标记中断，重连恢复。不改写原始事件，不伪造真实成交或资金流水。

阈值为第一版固定默认值，未增加配置页面。差异检查依赖独立账户读取，阈值不能代替定期查询。
目前主要能发现账户状态及成本差异，不能证明交易所自身历史完整。独立账户读取不是跨请求原子快照；
查询期间发生交易也可能触发重新建立基准，差异记录不等于已确认漏账。

## 持久化与重算

业务 ORM 新增 `equity_events`（不可改写证据）、`equity_points`（可重建结果）、
`equity_state`（最新状态/对账摘要）与 `equity_rebuilds`（任务审计）。
事件与派生点在同一事务入库；相同来源身份重试幂等，内容冲突拒绝覆盖。
API 进程通过 `database_path` 同目录的 `equity.lock` 互斥采集与重算；同一部署的 API worker
必须共享该路径，锁由操作系统在进程退出时释放。该锁不是跨机器协调机制。

一键重算只重放所选账户、当前环境与范围内已保存的完整估值证据，不请求下载历史，
不重建或删除原始交易数据，不会使未知历史变完整。全范围重算的终点固定为任务提交时间。
后台任务把范围内派生点在单个数据库事务中替换；失败回滚派生结果，保留原始证据及失败审计。
账户持久化事务锁阻止重算与增量更新互相覆盖。大范围重算可能延迟该账户的新事件入库；
缓冲为 256 份状态，溢出会明确记录中断并重新对账，不承诺无限积压。
任务显示 queued/running 和最终处理数量；服务重启将未完成任务标为 interrupted，可重新提交。

## API 与 LIVE

| 接口 | 用途 |
| --- | --- |
| `GET /api/exchange-accounts/{id}/equity-candles` | 登录读取真实派生 K 线 |
| `GET /api/exchange-accounts/equity-candles` | 相同环境的账户按事件时间对齐汇总；不相加独立 OHLC |
| `GET /api/exchange-accounts/{id}/equity-status` | 登录读取最新状态、估值及对账摘要 |
| `POST /api/exchange-accounts/{id}/equity-rebuilds` | 管理员发起重算，JSON 可选 start_ms/end_ms；202 返回任务 |
| `GET /api/exchange-accounts/{id}/equity-rebuilds/{job_id}` | 登录读取同账户、同环境的任务状态 |

K 线按有序数据库游标流式聚合，不将全部原始历史加载到内存；总览共用一个游标，避免按账户占满连接池。
K 线沿用 5m/15m/1h/4h/1d 与 limit（1–500）。`is_sample=false`，新增 status、gaps、warnings。
没有证据返回空数组。含明确缺口的整个粗周期不发布 OHLC，前端用空白时间点保留缺口；
新基准开始的第一个周期可能因此为空。总览要求每个参与账户都有不超过 30 秒的有效观察值；
配置同时含正式及测试账户时，提示选择单个账户，不合并资金。
LIVE 选择单账户后可点击“重算资产曲线”，关闭页面只停止进度查询，后台重算继续。
需要向持锁进程提交命令，其他 worker 返回 409。现有鉴权、管理员及来源保护继续适用。

## 部署与验证

新增迁移 `a9f42e716c08`，未在生产数据库自动应用。先停止旧 API，经明确授权应用迁移，
再启动 API；旧结构的启动检查会拒绝运行。生产默认启用采集，`EQUITY_ENABLED=false` 可关闭。
关闭采集后历史查询仍可用，超过新鲜度窗口会显示中断；无采集持锁进程时重算返回 409。
开发测试 Settings 默认关闭采集，避免测试账户触发外部网络。旧式非 ORM 测试后端不新增第二套持久化，
曲线返回 unavailable/空数组，写入返回不可用或未持锁。

现有相关验证命令：

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest tests/api/equity_test.py tests/api/exchanges_test.py tests/api/orm_storage_test.py tests/connectors/binance/valuation_test.py tests/connectors/binance/account_queries_test.py -q
cd web
npm run lint
npm run test:frontend
npm run build
```

原生连接器测试使用本机 HTTP/WebSocket 模拟及虚构凭据，不进行真实交易。
