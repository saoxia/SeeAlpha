# 连续分钟行情层

独立接通本地历史、REST 补齐和 WebSocket 实时分钟行情，为后续交易运行层提供
连续的已闭合 K 线及就绪状态。仅使用公开行情接口，不读取账户密钥、不下单，
不修改 `forward_validation/`、研究冻结源、历史 Catalog 或原有策略。

2026-10-27: Bar subscriptions and historical requests use a data-only native Binance LiveNode. Native trade events supply freshness heartbeats; the published/persisted contract remains closed-minute OHLCV. SocketStateChanged drives disconnect gating.

## 本版范围

统一单位为 Binance USD-M 永续 **1 分钟 OHLCV**。唯一键为交易对和 UTC 开盘时间；
转换为 Nautilus Bar 时，事件时间沿用历史管道的收盘边界前 1ms。
策略可继续从同一条分钟流聚合 5m、15m、小时及日线。

本版不是逐笔行情或盘口层，不混合历史 `trades` 与实时 `aggTrades` 的编号，
不生成伪造 TradeTick，也不承诺逐笔成交数、主动买卖量等特征一致性。
现有指标缓存不自动切换；消费方从回放分钟线恢复自己的指标。

## 运行

使用冻结的本地环境，无需安装或升级依赖。默认 BTC/ETH、135 天分钟预热。
首次运行会读取已有 Catalog，再通过 REST 补齐其缺失部分；Catalog 为空也可补齐，
但首次请求和磁盘写入会更多。预热长度必须覆盖具体策略的最长状态窗口，135 天不是
适用于所有未来策略的自动保证。短预热参数仅适用于连通性验证。

```bash
.venv/bin/python -m market_data.service serve
.venv/bin/python -m market_data.service status
```

有界连通性验证使用独立目录，不与正式数据混用：

```bash
.venv/bin/python -m market_data.service serve \
  --root /tmp/nautilus-minute-smoke --warmup-minutes 5 --duration-seconds 90
```

配置项包括 `--symbols BTCUSDT ETHUSDT`、`--catalog`、`--root`。
同一数据目录的交易对清单固定；更换清单应使用新目录。重启保留原起点与所有缺口，
不会把起点推进到最新时间以假装完成补齐。增加预热长度若超出已保存历史，应建立
更早起点的新目录；缺少所需预热时不会进入 READY。

服务在前台运行，Ctrl+C 停止；本次实现不自动安装常驻服务。
`--duration-seconds` 限制采集主循环时长，初始化连接、网络超时和清理时间另计。

## 数据与就绪

默认目录 `data/market_data/binance_um_1m/`：

- `minutes.sqlite`：闭合分钟线、来源核验记录、连续检查点、缺口及冲突证据。
- `status.json`：原子更新的状态、各市场连续进度、实时消息时间、后台任务及最后错误。
- `writer.lock`：进程级排他写锁；第二个采集进程直接拒绝启动。

SQLite 使用 WAL 和 FULL 同步。数据和连续检查点在同一事务提交；重启重新检查
已提交数据的连续前缀。重复输入只增加来源核验，不重复新增分钟线；乱序到达先落盘，
前面的洞补齐后才推进连续检查点。未闭合分钟线只作为实时连接心跳，不保存为闭合行情。

启动顺序：校时 → 连接 WebSocket 并开始缓冲 → 分日读取 Catalog → REST 补洞和重叠核对
→ 消费方回放 → READY。REST 分页有固定上限，空页、内部分钟缺口、异常值均阻止就绪。
历史加载与 REST 请求期间仍接收实时消息；缓冲溢出触发降级和重新补齐。

| 状态 | 含义 |
|---|---|
| WARMING_UP | 启动后历史或消费方状态尚未准备完成 |
| READY | 所有配置市场的分钟覆盖、预热、实时心跳、时钟及来源校验均通过 |
| DEGRADED | 缺口、断线、过期行情、异常时钟、冲突或消费方错误；禁止新增风险 |
| STOPPED | 进程已正常停止 |

READY 仅代表本层的数据条件，**不代表账户对账、订单恢复或策略盈利条件已通过**。
断线、停更、重连后必须重新补齐及核对重叠；单凭收到一条新行情不会重新就绪。
连接正常而缺少某个市场消息，也会降级。默认实时消息最大陈旧时间 15 秒，分钟闭合
保留 2 秒交付宽限；服务器时钟以单调时间续算，校时有效期 120 秒。

`status` 命令会拒绝过期的 READY。文件是运维视图，不是交易授权；交易消费者应使用
进程内 `Feed.check()` 和每个 `Delivery.allow_new_risk`，不能缓存 JSON 中的 READY。

## 策略接入契约

`Feed` 接受 `on_delivery(Delivery)` 回调；`Delivery.bar.to_nautilus(instrument)`
转换为现有策略使用的标准 Bar。回调按每个市场的时间递增调用，分钟不重复。

- `allow_new_risk=False`：启动预热、重启恢复、断线补齐或过期数据，只更新指标和状态。
- `allow_new_risk=True`：就绪期间新到达、连续且及时的 WebSocket 闭合分钟线。
- 初次进入或恢复 READY 时，所有积压线一律回放；下一根及时的实时线才可能允许新增风险。
- 消费方抛出异常后锁定降级，需重启并恢复消费方状态，不能自动重试可能已产生副作用的调用。

**不能把回放回调直接接到现有策略会下单的 `on_bar()`。** 后续交易运行层需要把
“更新指标/状态”和“允许交易决策”明确分开，并在执行端再次检查门槛。本层未改写冻结策略，
也不保证外部下单副作用的跨进程 exactly-once；那属于订单幂等及账户恢复阶段。
撤单与风险退出不应由本层数据就绪门槛阻断。

## 日终核验与冲突

运行期间核对前一个完整 UTC 日：读取当日已有 Catalog，再对照官方 REST K 线，
保存来源核验记录，不重写历史 Catalog。新出现的迟到历史归档可通过
`Coordinator.audit_day()` 再核对；本层不自动下载逐笔 ZIP，也不将分钟数据伪装成逐笔归档。

同一分钟价格必须一致；成交量只允许 `max(1e-8, |volume| × 1e-12)` 的数值误差。
超出误差的来源差异会回滚整页，持久保存原值、新值、来源和时间，锁定 DEGRADED；
重启不会清除此冲突。不自动选一个来源覆盖已消费行情。需要查明原因后保留原库，
以独立目录和明确的新起点重建、重放，并重新验收。

持续停机跨过多个自然日时，分钟缺口仍全部补齐；自动日终核验只针对最近完整日，
不是全历史每日归档复审。真实无成交、停牌等导致缺分钟时同样暂停，不自行造零成交线。

## 验证

```bash
.venv/bin/python -m pytest tests/market_data -q
.venv/bin/python -m ops.check_project
```

覆盖启动交接、REST 分页与缺口、重复/乱序、重启检查点、双市场门槛、断线补齐不追单、
来源冲突持久化、消费方失败、时钟失效、限流退避、Catalog 桥接与 Nautilus 时间戳。
实际网络联通和长时间运行验收须独立记录，离线测试通过不能代替网络验收。

接口参考（2026-09-28 核对）：
[Binance USD-M REST 行情](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#klinecandlestick-data)、
[WebSocket K 线](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market#klinecandlestick-streams)。
