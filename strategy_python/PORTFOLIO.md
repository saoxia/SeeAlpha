# 配置驱动的多策略回测

多个 Strategy 实例注册到同一个回测引擎，共用一个 Binance 永续账户。
默认 `execution=netted`：两个及以上成员只申报目标，由组合协调器净额后发一笔差额订单。
单成员，或显式 `execution=independent`，仍由每个策略自己下单。不必为每组策略编写新入口。

## 文件职责

- `_portfolio/*.json`：运行清单，定义成员、启停、参数、风险权重、行情及账户设置。
- `portfolio.py`：注册可用策略，读取并校验清单，创建实例，连接共享预算和净额协调器。
- `_execution/target_netting.py`：把带符号目标相加，并计算相对账户持仓的差额。
- `_execution/portfolio_coordinator.py`：唯一发单路径；止损触发前不改变净目标。
- `portfolio_backtest.py`：统一回测入口，负责历史行情、引擎及报表。
- 各策略目录：维护交易逻辑、参数和单策略回测。

## 运行

默认清单是 `_portfolio/mean_reversion.json`，保留均值回归多空、500 USDT、5 倍杠杆、
135 天预热、30% 总计划风险及各 50% 的风险权重：

```bash
uv run python -m strategy_python.portfolio_backtest \
  --start 2026-08-01 --end 2026-08-08
```

明确选择清单：

```bash
uv run python -m strategy_python.portfolio_backtest \
  --config strategy_python/_portfolio/mean_reversion_three.json \
  --start 2026-08-01 --end 2026-08-08 --catalog-only
```

第二个示例启用三个成员：做多、做空，以及布林带周期为 30 的另一份做多实例。
`--catalog-only` 只读取已有行情，不下载或刷新远端归档；使用前确认区间及预热数据完整。
时间为 UTC，区间左闭右开。省略日期时仍使用最近七个完整自然日。

均值回归做多、趋势跟随做多和趋势跟随做空三策略共用账户：

```bash
.venv/bin/python -m strategy_python.portfolio_backtest \
  --config strategy_python/_portfolio/mean_reversion_long_trend_both.json \
  --start 2021-01-01 --end 2026-09-01
```

该清单使用 500 USDT 初始资金、5 倍杠杆、1 分钟输入线及 135 天预热，
三个成员沿用各自当前默认参数。`risk.mode=none` 表示仅共用账户，各策略保留自身
风控，不额外限制组合总计划风险；趋势策略尚不支持 `shared` 模式。

小时反弹做空加入后的四策略组合：

```bash
.venv/bin/python -m strategy_python.portfolio_backtest \
  --config strategy_python/_portfolio/mean_reversion_long_trend_both_pullback_3x.json \
  --start 2021-01-01 --end 2026-09-01
```

该清单在上述三策略默认参数基础上启用 `PBS`（`pullback_short_1h`），
单笔计划风险 `risk_fraction=0.015`，开仓名义金额上限 `max_notional_fraction=3.0`。
共用账户仍为 500 USDT、5 倍保证金杠杆；3 倍是 PBS 成员的开仓上限，
不是组合总杠杆上限，也不是每笔固定满仓 3 倍。沿用 `risk.mode=none`，
各成员共用账户权益和保证金，实际组合成交与收益需通过组合回测确认。

## 配置清单

```json
{
  "name": "mean_reversion",
  "market": {
    "instrument_id": "ETHUSDT-PERP.BINANCE",
    "bar_type": "ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL",
    "data_symbol": "ETHUSDT",
    "interval": "1m"
  },
  "account": {"starting_balance": "500", "leverage": "5"},
  "backtest": {"warmup_days": 135},
  "risk": {"mode": "shared", "total_fraction": "0.30"},
  "strategies": [
    {"id": "LONG", "strategy": "mean_reversion_long_5min", "enabled": true,
     "risk_weight": "0.60", "parameters": {"bollinger_period": 20}},
    {"id": "SHORT", "strategy": "mean_reversion_short_5min", "enabled": true,
     "risk_weight": "0.40", "parameters": {}}
  ]
}
```

- `name`：输出目录名称，使用字母、数字、下划线。
- `id`：唯一成员编号，也是订单编号标签和风险预算键。同一策略可以注册多个不同编号的实例。
- `strategy`：策略注册名称。当前提供 `mean_reversion_long_5min`、`mean_reversion_short_5min`、`trend_following_long_5min`、`trend_following_short_5min`、`pullback_short_1h`、`mean_reversion_short_v2_5min`。
- `enabled`：默认 `true`；设为 `false` 后不创建实例、不占预算。
- `parameters`：该策略的配置覆盖；未指定的参数使用策略默认值。参数名称和类型会校验。
- `risk_weight`：共享风控下启用成员的权重，必须为正数、合计为 1。不自动归一化；禁用一个成员时须明确重新分配剩余成员的权重。

成员不能覆盖清单中的合约、输入 K 线、交易起点或策略/订单编号。
当前所有成员使用同一合约、同一输入周期，支持 Binance 永续 1s、1m、5m。
可用 `--interval 5m` 覆盖清单的输入周期和 BarType，所有成员随之更新；高周期由输入线
在内存中聚合。输入必须整除策略的最小信号周期，当前三个策略均为 5m。
开启检查后 Pipeline 默认只补缺失；`--check-data --force` 显式重建含预热日期的 TradeTick 和输入周期，
不会重写其他周期。`--catalog-only` 与 `--force`、`--refresh-archives` 互斥。
`warmup_days` 应覆盖所有成员所需的预热长度；修改策略窗口时需同步检查该值。
多合约及不同输入周期的组合行情加载尚未实现。

## 共用账户、净额与共享风控

账户只注入一次初始资金；手续费、保证金、已实现盈亏统一结算。

`execution` 缺省为 `netted`：

- 两个及以上启用成员绑定 `PortfolioCoordinator`，并最先注册。
  每根 K 线的净额放在同一时刻的警报里，等全部成员处理完再计算，因此不依赖策略 id 的先后。
  账户使用 NETTING。协调器读取 Nautilus `Portfolio.net_position`，只提交净目标与
  该持仓的差额。多空目标在发单前抵消，不会同时送出一对相反的市价单。
  停止回调按注册顺序发生，协调器因此先把账户净持仓一次平掉，对应成员原来的
  `close_positions_on_stop`。
- 成员原有的入场单不再发到交易所。限价和止损要等下一根 K 线触及后，才变成目标的一部分。
  可成交的多空数量先在内部对冲，剩余差额才发一笔市价单，成交再按比例回写到成员。
- 保护性止损和应急退出不直接发单。止损在触发前不缩小净目标，触发后该成员目标按退出数量
  下降，并进入同一轮净额。若对面目标正好接住，账户不再发单，但成员仍收到成交回写，
  恢复和冷却不会丢。应急市价单在当根协调器回合就生效。
- 只有一个启用成员时不插入协调器，订单、止损和持仓与单独回测相同。
- `execution=independent` 保持原来的 HEDGING：各策略自己下单，同一合约可以同时持有多空。
  这是兼容路径，不是净额。

NautilusTrader 2.0.0rc7 被复用的部分是 Strategy 生命周期、`Portfolio.net_position`、
订单工厂、成交回报，以及仍会检查差额订单的 RiskEngine。`FixedRiskSizer` 只按一笔
入场和止损计算数量。`ExecutionAlgorithm` 只把一张订单拆成子单。`Controller` 只负责
启停组件。OMS 的 NETTING 合并的是成交后的持仓。这些都不是跨策略的目标净额，所以
净额函数和协调器是项目补上的。没有使用信号总线传递目标：它是异步的，无法保证
同一根 K 线里先收齐目标再发单。

共享风险预算仍然只在新周期入场时限制各成员还能使用的额度，不会把多空目标抵消。

`risk.mode` 必须明确指定：

- `shared`：全部启用成员必须接入共享风险预算接口。每次新周期入场，按策略原定仓、
  成员剩余额度、账户总剩余额度的最小值限仓。预算使用成员编号，不限定多空或成员数量。
- `none`：仅共用账户，保留各策略自己的风控；不提供组合计划风险上限。此模式不接受
  `total_fraction` 或成员 `risk_weight`，报告会明确记录未启用共享风控。

原均值回归两个策略已适配 `shared`。趋势策略、`pullback_short_1h` 和
`mean_reversion_short_v2_5min` 尚未适配共享风险接口，
加入共享预算组合会在读取行情前报错；可以显式使用 `none` 共用账户，或先完成风险适配。
不能通过给趋势策略填一个权重来假装它已受共享限额控制。

以 500 USDT、总比例 30%、两个成员各 50% 为例，总计划风险额度为 150 USDT，
每个成员最多预留 75 USDT。这是风险额度分配，不是两个虚拟账户；当前策略读取的是
同一账户余额及该合约总浮盈亏。

均值回归的承诺按含手续费和缓冲的四层完整周期止损成本计算，待成交首仓也占额度。
首仓成交后转为周期预留，加仓使用既有周期额度；分批止盈不提前释放。
全部平仓且相关入场单结束后才释放，等待撤单确认的订单继续占额度。
组合共享风控下更新首仓先撤旧单，确认后重新核准；单策略入口保持原有更新方式。

该限制作用于新周期准入，不是最大回撤保证。跳空、止损滑点或权益下降可能令既有承诺
超过当前比例，此时限制新周期，不自动强平既有持仓。各成员空闲额度不自动相互借用。

## 报告

结果写入 `strategy_python/_report/backtest/portfolio/<name>/<run_id>/`：

- `account.csv`：唯一共用账户。
- `fills.csv`、`positions.csv`：全部成交和持仓，带实际策略编号。
- `strategy_01_*`、`strategy_02_*` 等：按启用成员的清单顺序分别导出的成交、持仓明细。
- `parameters.json`：清单路径、完整解析清单（含禁用成员）、实际实例配置、成员编号映射和风控模式。
- `summary.json`：整体指标及 `by_strategy` 成员统计，包括已平仓净收益、周期数、胜率和已实现收益回撤。
- `report.html`：整体多/空收益曲线、各策略贡献统计和可下载的成员明细。

当前曲线及分项统计采用完整持仓平仓后计入已实现净收益的口径，未平仓浮盈亏不计入，
不能把已实现收益回撤当作账户权益最大回撤。分项没有单独账户；组合结果也不是独立回测收益相加。

## 扩展与实盘

增加已注册策略或同一策略的参数变体，只需添加清单成员。新增策略实现则在
`portfolio.py` 的 `STRATEGY_REGISTRY` 注册配置类和策略类，统一回测入口无需修改。
若启用共享预算，该策略必须实现预算绑定和同口径的额度占用、准入限制与释放。

未来 `portfolio_live.py` 可以读取同一清单，将 `create_strategies(start, config)` 返回的
多个独立实例注册到同一个 `TradingNode.trader`。实盘入口尚未实现，交易所连接、
预热、启动对账以及持仓/挂单/周期风险状态恢复仍需另行完成。

## 旧调用迁移

原命令的 `--starting-balance`、`--total-risk-fraction`、`--long-weight` 已移除，
分别改为清单中的 `account.starting_balance`、`risk.total_fraction` 及各成员 `risk_weight`。
原 Python `long_overrides` / `short_overrides` 改为相应成员的 `parameters`。
只使用日期参数的原默认启动命令仍然有效。

## 数据检查开关

组合回测默认直接读取已有 Catalog，缺失日期或 K 线跳过，整个区间没有数据时跳过
本次回测；只有 `--check-data` 才开启归档检查和补齐。`--force` 和
`--refresh-archives` 需配合 `--check-data` 使用。
原 `--catalog-only` 保留兼容，效果与默认行为相同，不能与检查、刷新或强制重建同时使用。
Python 入口对应 `check_data=False/True`，默认 `None` 使用本地数据。
跳过空数据回测时返回 `None`，不创建回测报告。
