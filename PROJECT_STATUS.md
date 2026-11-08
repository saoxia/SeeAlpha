# SeeAlpha 项目状态与待办

产品定位（2026-10-27）：基于 NautilusTrader 的多资产量化交易系统，面向加密资产、
期货、股票等市场，以及现货、永续合约、期权等交易产品。
当前主要实现是 Binance USD-M 永续的公开行情、研究回测和管理端。期权行情适配器、
期权历史数据和 USD-M 资金流水台账已经在代码中，尚未接入真实账户或实盘执行。
其他市场仍需接入和验收。详见 [产品范围与当前实现](README.md#产品范围与当前实现)。

状态核对日期：2026-10-31。下文「当前检查」是这次在干净环境里实际跑出来的结果。
各研究小节里的测试数量是该日记录，不代表当前总数。

## 当前检查（2026-10-31）

环境：Python 3.14.9、NautilusTrader 2.0.0rc7、pandas 3.1.0、Ruff 0.17.2。
未使用交易所凭据，未下单。

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
cd web && npm run test:frontend
```

- Ruff：通过。
- pytest：`2 failed, 1573 passed, 37 skipped, 1 warning, 18 errors`（66 秒）。
  - 失败 2 项：`tests/core/environment_test.py` 要执行 `/bin/zsh`，本环境没有这个解释器（`FileNotFoundError`）。同一测试的 Bash 参数通过。
  - 错误 18 项：全部是 `tests/pipeline/data_management_test.py` 的 `dataset` 夹具。`find_rust_converter()` 为 `None`，Python 回退在 `engine/pipeline/catalog.py` 的 `_decimal` 把数值转成 `decimal128(38, 16)` 时失败，异常为 `pyarrow.lib.ArrowInvalid: Rescaling Decimal value would cause data loss`。
  - 跳过 37 项：26 项因为未设置 `TEST_POSTGRES_URL`（`tests/api/operations_test.py`、`tests/api/orm_storage_test.py`）；11 项因为 Rust 转换器未构建（`tests/pipeline/convert_binance_trades_test.py`，原因是 `Rust converter has not been built`）。
  - 警告 1 条：依赖里的 `StarletteDeprecationWarning`（`fastapi/testclient.py` 使用 httpx）。项目代码没有再发出时间接口或 `float_precision` 警告。
- 前端 `npm run test:frontend`：29 项通过，0 项失败。

检查任务本身仍由 `api/management/jobs.py` 的 `run_checks` 执行，结果写到
`logs/web/checks/<时间戳>/` 和 `logs/web/checks/latest.json`。`logs/` 被 gitignore，
全新 clone 里没有这份文件；跑一次项目检查后才会生成。

## 当前实现

默认研究数据集仍是 `engine/pipeline/binance_sources.toml` 中的 BTC-USDT、ETH-USDT、SOL-USDT。
本地开发服务是前端 `127.0.0.1:3000` 和 API `:8000`。

### 前后端拆分第二阶段（已完成）

已在 [PR #1](https://github.com/saoxia/SeeAlpha/pull/1) 合并。项目检查、BTC/ETH 策略回测、
报告浏览和任务日志在 FastAPI `api/management/`（`router.py`、`jobs.py`、`service.py`、
`artifacts.py`、`storage.py`），路径为 `/api/management`，复用现有用户会话；启动和取消需要管理员。
Next.js 主导航有 RESEARCH 页：`web/app/components/research/research-page.tsx`，
入口在 `web/app/components/navigation.tsx`。`web/server.py`、`:8765` 服务和 `web/static/*` 已不在树中。

管理任务锁是 `.local/run/web/server.lock`（`api/management/storage.py` 的 `runtime_lock`）。
拿到锁的进程迁移遗留的 `data/web_console/`、把仍标为运行中的任务标成中断，并执行启动、取消和子进程。
没拿到锁的进程照常提供其余 API，`/api/management` 仍可查询；在该进程上启动或取消由
`JobReader` 抛出 `NotOwnerError`，路由返回 **409**，正文为「当前进程未持有管理任务锁，不能启动或取消任务。」
同一时间仍只有一个此类任务。多 worker 或另一个端口可以同时提供 API，但锁不会在运行中途被接管；
持锁进程退出后，下一个启动并拿到锁的进程才接受启动和取消。因此启动或取消请求必须到达持锁进程。

任务记录在启用 ORM 时写入 `management_jobs` 与 `backtest_reports`，日志在 `logs/web/`，
报告原文件在 `data/reports/backtest/`。

### 已补记的现有功能

这些功能此前未写入本文件，路径以当前代码为准。

- **Binance 期权适配器**：行情客户端 `engine/connectors/binance/options_client.py`（`BinanceOptionsDataClient`），只读账户 `engine/connectors/binance/options_account.py`，执行客户端 `engine/connectors/binance/options_execution.py`。公开行情解析在 `engine/connectors/binance/options.py` 与 `engine/connectors/binance/options_parsing.py`。API 没有注册该执行客户端，也没有实盘策略调用它。说明见 [期权适配器](docs/BINANCE_OPTIONS_ADAPTER.md)。
- **USD-M 现金流台账**：`engine/connectors/binance/income.py` 读取签名 `GET /fapi/v1/income`；`api/operations/service.py` 的 `_sync_income` 写入既有操作账本，事件类型为 `cashflow`。查询仍走 `/api/operations`。说明见 [操作记录](docs/OPERATIONS_API.md)。
- **期权数据**：离线任务在 `engine/pipeline/options_data.py`，经 `/api/data/*` 传 `product=options`（`api/market_data/`）。历史 K 线为 1m、5m、15m；近期成交有上限，不表示完整历史 Tick。期权连续采集由引擎的 feed 进程承担。方案见 [期权数据](docs/OPTIONS_DATA_PLAN.md)。
- **常驻交易引擎**：`engine/` 持有连接器、连续行情和完整 `engine/pipeline/`（含 Rust 转换器与 Catalog）。`python -m engine` 提供内部 HTTP，并启动采集、历史补齐和一次性 `data_management` 任务。SQLite 记录订单幂等、成交、持仓、缺口和运行期订阅。API 保存订阅意图和任务索引，不再自行启动这些进程，也不再构造账户/手动/估值 LiveNode。策略以 `packages/` 中的可下发包登记，不再使用已删除的研究目录。
- **托管行情数据服务**：业务配置在 `market_data_policies`。引擎按该意图启动 `engine.feed.managed` 与 `engine.pipeline.pair_data --service`。`api/main.py` 每 15 秒刷新配置，每 5 秒调用 `api/market_data/monitor.py` 的 `ServiceMonitor.sample` 把健康和积压写入文件。离线准备、删除和裁剪由引擎执行 `engine.pipeline.data_management`。
- **主题设置**：`web/app/features/appearance/theme.ts` 把偏好写入浏览器键 `seealpha.interface-theme`（像素风 / 液态玻璃）。切换组件是 `web/app/components/appearance/theme-switch.tsx`，设置页是 `web/app/components/settings/appearance-settings.tsx`。没有对应的后端设置接口。

### 业务数据库

ORM 同时支持 SQLite 和 PostgreSQL（`api/db/database.py`、`api/db/orm.py`）。
开发和自动化测试默认使用 SQLite。PostgreSQL 是可选项，用 `DATABASE_URL` 或私有连接文件配置。
API 进程要求显式连接；未配置时不会自行建库，也不会在连接失败后改用另一种数据库。
连接与迁移见 [API 持久化说明](api/README.md#通用-orm-业务持久化)。

Binance 原生只读查询在 `engine/connectors/binance/queries.py`，不经过旧 CCXT 客户端。账户快照由引擎进程调用。
范围见 [Binance 原生 API 说明](docs/BINANCE_NATIVE_API.md)。

### 警告

约 180 条上游时间接口弃用提示已随 NautilusTrader 2.0.0rc7 升级消除。本次全量 pytest 的警告汇总里没有这类提示。

去掉参数之前，同一命令还有 **78** 条 pandas `Pandas4Warning`：`The 'float_precision' argument is deprecated. Use the default float precision instead.`
全部指向当时的 `engine/pipeline/convert_binance_trades.py`（原 `pipeline/convert_binance_trades.py:76`）（`tests/pipeline/bar_features_test.py` 25 条、`convert_binance_trades_test.py` 29 条、`data_management_test.py` 22 条、`pair_repair_test.py` 2 条）。
pandas 3.1 起 `high`、`legacy` 和 `round_trip` 共用同一转换器。`iter_trade_archive` 已不再传入 `float_precision`。
抽样比较省略该参数与三种旧模式，数值列一致。修改后的全量测试不再出现这条警告。

## 仍未完成

- [ ] 行情 feed 的长时间运行验收，以及具体策略的预热验收。短时 5 分钟预热验证不替代这两项。运行说明见 [feed/README.md](engine/feed/README.md)。有界连通性检查使用独立目录，不把结果写进仓库：

  ```bash
  .venv/bin/python -m engine.feed.service serve \
    --root /tmp/nautilus-minute-smoke --warmup-minutes 5 --duration-seconds 90
  ```

  旧文档指向的 `data/market_data/validation/20260928/smoke.json` 不在仓库中（`data/**` 被 gitignore，全新 clone 没有该文件）。
- [ ] Binance 私有接口尚未使用真实账户凭据验收。
- [ ] 实盘执行节点尚未接入 `OperationService.record_project_event`（`api/operations/service.py`）。当前只有 `tests/api/operations_test.py` 调用该入口。回测不应写入这个实盘账本。

前向观察及其健康页、样本质量、备份、恢复和到期评估已从代码中删除。
当前保留离线研究与回测、连接器、连续行情服务、托管行情数据和项目管理端。
下面各节是历史研究记录，不表示仍有后台任务在运行。

## 独立策略优化（2026-09-22）

- [x] 新增独立均值回归多头候选：完整日线趋势过滤，以及24小时／布林距离退出和中轨重新准入规则组。
- [x] 预先固定原基线、仅过滤、仅退出、两者组合四组，在BTC/ETH和两档费用下对比；组合为主候选，不根据事后最高收益换选。
- [x] 新增时序、缺口、退出、风险预算保持及零交易统计检查；当日全项目 **1,175项测试通过**，历史研究和前向冻结来源校验通过。
- [x] 16组完整历史回放及逐项门槛检查完成，三种新增规则均未通过。主候选最长持仓缩至24小时，但原费率BTC净收益从−29.74降至−50.40 USDT，ETH从−52.15降至−97.47 USDT；费用压力后也未通过，不替换默认策略。
- [x] 三组可对照基线逐笔复现历史参考，全部回放通过开仓约束、价格精度、持仓归零和现金流检查；保留已有撤单目标不存在记录、分钟数据缺口及统计工具修订说明。

上述研究材料已随 `strategy_python/` 删除，不再作为可运行来源。

## 入场质量优化（2026-09-22）

- [x] 新增独立成本门槛、跌破收复确认和5分钟订单到期；完整闭合行情驱动，保留原仓位预算，使用固定退出框架。
- [x] 完成BTC/ETH、两项规则、两档费用共8组完整回放，加2组严格成交复核；核验复用8组原有对照，固定主候选与判断门槛。
- [x] 主候选原费率最大回撤由BTC 14.26%／ETH 22.53%降至9.45%／16.82%，但净收益由−29.74／−52.15降至−36.06／−68.19 USDT；费用压力下亏损减少，仍未形成跨市场一致改进，不替换默认策略。
- [x] 完成现金流、过期订单、退出贡献和月度不确定性复核；当日全项目 **1,186项测试通过**，冻结来源保持一致。

上述研究材料已随 `strategy_python/` 删除，不再作为可运行来源。

## 收复后持续走强优化（2026-09-22）

- [x] 新增后续完整一分钟收盘突破确认线高点后入场，限制买入价格并保留确认后的5分钟期限；重新检查剩余盈利空间，仓位预算不变。
- [x] 完成两市场、两档费用和严格成交共6组完整回放，核验10组历史参考；控制组逐笔复现上一轮。
- [x] 相较上一轮，原费率BTC净收益从−36.06改善至−26.02 USDT，回撤9.45%降至6.63%；ETH净收益从−68.19降至−69.22，回撤16.82%降至15.44%。费用压力下累计亏损减少，6组全期及近期收益仍全部为负，不替换默认策略。
- [x] 新候选平均单笔净亏损增加，主动成交费用更高；保留全部失败结果、入场时序、价格上限及成本分解。当日全项目 **1,203项测试通过**，冻结来源核验通过。

上述研究材料已随 `strategy_python/` 删除，不再作为可运行来源。

## 连续分钟行情层（2026-09-28）

- [x] 新增独立 `feed/`：已闭合 1 分钟 OHLCV 的 Catalog 读取、REST 分页补齐、WebSocket 缓冲和衔接；不混合 trades/aggTrades 编号。
- [x] SQLite 原子落盘和连续检查点，重复去重、乱序缓存、缺口阻断、来源冲突持久化及重启重查。
- [x] WARMING_UP／READY／DEGRADED 状态控制；断线、心跳或时钟异常后重新核对；恢复数据只回放，不补发历史信号。
- [x] 前一完整 UTC 日分钟数据核验，Nautilus Bar 转换及显式 replay/live 消费接口。
- [x] 当日新增 31 项测试；全项目 **1,307 项测试通过**，冻结来源和代码检查通过。
- [x] 公开接口短时验证：BTC/ETH 冷启动达到 READY，75 秒内各记录一根实时闭合线；同一目录重启 25 秒再次 READY。两次均正常停止，无账户或下单操作。该次产物不在仓库中。
- [ ] 长时间运行验收与具体策略预热验收。短时 5 分钟预热验证不替代这两项。

本版仅分钟 OHLCV，不提供逐笔还原、盘口或主动成交量特征；未自动启用常驻服务，
未接入交易账户或现行策略下单。

[运行与接入说明](engine/feed/README.md)

## 查看与复查

[运维与恢复记录](web/README.md) · [操作记录说明](docs/OPERATIONS_API.md) · [策略包示例](packages/ema_crossover/README.md)

2026-10-26 的开发环境记录是当日验收：当时 pytest 为 1,345 项通过。该数字不是上面的当前结果。
详情见 [开发环境说明](DEV_ENVIRONMENT.md)。
