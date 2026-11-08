# Binance USD-M 手动交易

2026-11-06 用户要求在 LIVE 与 DATA 之间新增交易页，确认首版市场为 Binance USD-M 永续，
并要求参照 Binance 交易页组织布局。导航为 LIVE → TRADE → DATA → RESEARCH → SETTINGS。

## 当前功能

- 使用 SETTINGS 已保存的加密 Binance 账户，明确区分实盘与测试网；不向浏览器提供密钥。
- 点击行情行的当前交易对打开 Binance 风格弹出列表：搜索、自选、全部/USDT/USDC 筛选及合约/成交额/价格/涨跌/资金费率排序。星标仅在本浏览器保存；COIN-M 未接入。列表打开时每 15 秒读取公开行情，失败或过期明确标注；缺失字段显示“—”。
- 公开行情包括 K 线、盘口、近期成交、24 小时行情、标记/指数价格及资金费率。
- 图表使用已安装的 TradingView Lightweight Charts，支持周期、缩放及可自定义的 MA、EMA、BOLL、RSI、MACD、成交量；不是需要单独授权的完整 TradingView Advanced Charts。
- 技术指标支持最多 24 组独立实例，同类指标可重复添加，分别显示/隐藏、设置、排序、删除。MA/EMA/RSI 周期为 1～500；BOLL 支持周期和标准差倍数，RSI 支持超买/超卖线，MACD 支持快/慢/信号周期。
- 曲线可选开/高/低/收、HL2/HLC3/OHLC4 来源、独立颜色、1～4px 线宽和实/点/虚线；成交量与 MACD 柱支持涨跌色。应用后更新，浏览器保留配置并恢复旧版指标开关。每次行情刷新只更新曲线数据，保留缩放和副图高度；设置变更时重新布局副图。
- 当前显示最近 300 根 K 线；指标预热不足时保持空白，不补造指标值。MACD 柱沿用 2 × (DIF − DEA)。指标仅供显示，不接入策略或下单风险判断。
- 平台、账户、产品及交易对在行情行左侧；全屏按钮隐藏导航，退出全屏或 Esc 恢复。
- 图表“更多设置”分别控制当前委托、持仓开仓价、历史委托价、损益两平价、强平价及订单草稿线。历史委托价不是成交价；账户快照过期后隐藏账户价格线。
- 快捷填价仅把主图点击位置填入限价单，副图点击不填价，仍需确认后提交。价格提醒在当前页面打开时检测公开行情跨越，不后台运行；换合约/周期或重载会清除。指标及显示偏好只在本浏览器保存，应用后的参数、样式、显隐、排序及删除结果在刷新或切换交易对/周期后继续沿用；空指标列表也会保留。
- 限价/市价、买卖、GTC/IOC/FOK、Post Only、Reduce Only / 双向平仓；不再手选持仓方向。
- 提交和撤单均显示账户、环境、合约及订单参数确认。持仓提供填入平仓数量；自动沿用账户持仓模式，用户核对买卖方向后确认。
- 当前合约持仓、当前委托、近 24 小时可发现的历史委托及既有账本中的成交。历史读取受原生对账覆盖范围限制。

这不是 Binance 全部交易功能的复制：止盈止损、跟踪止损、条件单、批量订单、调杠杆、
切换保证金/持仓模式、追加保证金、划转、全市场订单汇总和完整历史查询尚未开放。
使用账户在交易所已有的杠杆、保证金及持仓模式，不会在启动时修改这些设置。
下单区显示所选合约的实际杠杆倍数，替代原设置/持仓模式说明；只读 GET
`/fapi/v1/symbolConfig` 提供该值，空仓仍可查询。缺失、无效或快照过期显示“—”，
查询失败不阻断原生下单/撤单，提交命令不增加杠杆读取。
提交前只读获取实际持仓模式；双向开仓买→LONG、卖→SHORT，平仓买→SHORT、卖→LONG。单向使用 BOTH。
确认绑定预期模式，模式变化拒绝旧确认；平仓核对对应仓位方向和数量，保留原生 Reduce Only 风控。
旧 API 的显式 position_side 参数继续接受并核对实际模式，新页面省略该参数。不会切换账户模式。

## 调用与存储

```text
TRADE → Next.js 同源 /api/trading 代理 → 现有 API / 管理员及来源校验
  → ManualTrading（账户修订核对、账本审计）
  → 引擎 /accounts/{account_id}/orders（request_id 幂等写在引擎 SQLite）
  → engine.connectors.binance.manual → 引擎进程内的 LiveNode / Strategy / Binance factory
  → Nautilus RiskEngine → ExecutionEngine → Binance USD-M
```

只读账户入口不提交命令。锁定版本的公共 Python 节点未发布持仓模式与风险价格，
`manual.account_context` 使用固定官方 GET `/fapi/v1/positionSide/dual` 和 `/fapi/v3/positionRisk` 补充，
先读取服务端时间再签名，禁止重定向，限制响应体和超时，错误不暴露凭据/签名。
风险价格只用于显示，只有原生持仓的方向、数量及开仓价一致时才附加；缺失/零值不猜测。
下单和撤单只补充持仓模式，不依赖风险价格端点。`queries.account_node` 共享现有节点配置；只有手动入口显式
选择 manual 才关闭原生请求重试，并安装手动策略。每次请求在独立线程内拥有完整的原生节点生命周期，
沿用既有按需账户查询方式；节点停止不代表撤销交易所挂单。已有策略与组合执行路径保持原状。
前端行情默认由浏览器直连 Binance 公开 REST 初始化最近 300 根 K 线，再订阅 K 线、聚合成交、
24h 行情、标记价及前 20 档盘口。Market 与 Public WebSocket 分开连接，断线或 30 秒无消息
时退避重连并重新初始化；切换品种、环境、周期和离开页面时释放旧连接。不向浏览器提供密钥。
聚合成交仅供展示，不作为原始逐笔写入。账户仍为按需快照。
实盘环境下，当前窗口实际存在本地 K 线时提供“切换本地 K线”按钮；本地视图每 1 秒读取
`GET /api/data/chart`，合并最近 300 个周期内的 Catalog、保留的采集库及有效状态中的形成中尾部。
没有当前周期数据时保持空白，不以 Binance K 线填充。本地错误沿页面顶部提示展示；测试网不使用本地实盘数据。
切换仅改变图表 K 线，盘口和最新成交仍使用 Binance 直连；本地 K 线不能代替直连行情就绪检查。
页面对超过 60 秒的账户或行情快照禁止发起新订单，服务端提交还要等待 5 秒内原生报价及账户启动对账。
显示行情不充当原生风险报价；没有实时原生报价时不发送订单。

命令只接受已知合约格式与正数十进制文本，原生 instrument 验证数量、价格步长和最小金额；
不对用户数量/价格取近似值。RiskEngine 保留账户资金、订单及 Reduce Only 检查，ExecutionEngine 拥有状态机。
执行事件先在本机保存，再通过既有账本 ingest 保存；对账观察仍保留其真实来源，不把合成恢复成交作为真实成交。

账户快照发布不可逆的配置版本摘要，订单/撤单确认绑定该版本；账户凭据或环境变化后拒绝旧确认，
未知请求也不能用新凭据查询。摘要不包含可解密凭据。

API 状态目录下的 `trading/<account>/<environment>/<request UUID>.json` 保存请求摘要、
固定客户订单号、结果和执行证据。生产目录通常为 `.local/state/api/trading/`；不新增数据库表或迁移。
文件权限 600，写入采用临时文件、fsync 与原子替换。API 在发送前完成登记，账户文件锁防止多进程并发写。
同 UUID 不同参数拒绝，同参数重放只返回已有结果；节点或 HTTP 超时后不重发订单。
前端保留待核实请求，重新打开页面仍须通过查询核对；若交易所没有可确认的记录，保持待核实，
不能因为“查不到”就认定未发送。原生节点退出不撤销交易所已有订单。
请求结果文件属于幂等与恢复证据，不能作为普通缓存删除。

## HTTP

旧 `/api/trading/market` 快照路由及其 connector 查询已按用户要求移除；
图表使用浏览器公开订阅，合约选择器保留 `/api/trading/markets`。

前端代理等待上限为 90 秒，以容纳原生连接/对账与清理；浏览器交易请求等待上限为 75 秒，超时不重发。

全部入口要求管理员会话，继续使用现有 Origin / Sec-Fetch-Site 校验、无缓存响应与服务端凭据解密。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/trading/markets` | 原生完整合约目录与公开 24h 行情/资金费率；不受旧搜索入口 200 条上限影响 |
| GET | `/api/trading/accounts/{id}/state` | symbol 对应的账户与委托快照 |
| POST | `/api/trading/accounts/{id}/orders` | 带 UUID 和预期 environment 的显式手动订单 |
| POST | `/api/trading/accounts/{id}/cancel` | 带独立 UUID、environment 和客户单号的撤单 |
| GET | `/api/trading/accounts/{id}/requests/{uuid}` | 核对已登记请求，不重发 |

`status=completed` 表示结果已明确，包括风控拒单及交易所拒单，不表示必然成交。
`status=unknown` 表示结果待核实。订单当前状态以 `order.status` 为准。
不在 API 启动、页面加载、轮询、快照查询或测试中发起真实订单。

## 验证

```bash
.venv/bin/python -m pytest tests/connectors/binance/manual_test.py tests/api/trading_test.py -q
cd web
npm run lint
npm run test:frontend
npm run build
```

原生集成测试使用本地 HTTP/WebSocket 模拟交易所及虚构凭据；覆盖限价、市价、撤单、拒单、
资金不足、精度与过期报价。没有执行真实账户交易，真实账户权限、交易所网络和实盘成交尚未验收。

参考：[Nautilus Binance 适配器](https://nautilustrader.io/docs/latest/integrations/binance/)、
[Binance USD-M 订单接口](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order)。

账户/手动节点的异常帧在所属工作线程同时清理显式原因与上下文链，包含退出阶段的宿主错误；
未分类手动异常只记录动作、类型和代码位置，保持凭据与签名 URL 不出日志。
