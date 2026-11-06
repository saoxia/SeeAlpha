# Python 策略工程约定

[BTC-USDT现行四策略与历史迁移结果](BTC_STRATEGIES.md)：趋势跟随与均值回归，多空各一个；重复实现共用一个来源。

四组原/新版的[参数计算来源与迁移约定](PARAMETER_SOURCES.md)已落实到各策略配置和领域模块；[待讨论参数](PARAMETER_QUESTIONS.md)单独记录缺乏校准依据的阈值和曲线。

`strategy_python/` 保存项目内全部 Python 策略。工程采用“薄 Strategy 编排层 + 领域模块”
结构，固定数据流为：

```text
Market Data → Indicators / Features → Signal → Position Target
            → Risk Overlay → Final Target → Execution → Order
```

目录形式服务于职责和依赖边界，不要求简单策略创建没有实际职责的空文件。

当前具体策略：`mean_reversion_long_5min`（均值回归做多）、
`mean_reversion_short_5min`（均值回归做空）、`trend_following_long_5min`（趋势跟随做多）、
`trend_following_short_5min`（趋势跟随做空）、
[`pullback_short_1h`](pullback_short_1h/README.md)（下跌趋势中的小时反弹做空）、
[`mean_reversion_short_v2_5min`](mean_reversion_short_v2_5min/README.md)（周末五分钟均值回归空头，3～5倍开仓上限）。
均值回归和趋势跟随均按方向独立配置和优化，不互相导入，也不提供双向切换。
根目录 `portfolio.py` 根据 `_portfolio/*.json` 清单组装策略。
多个成员且 `execution=netted`（默认）时，协调器把各成员目标净额合并，
只交易净目标与账户持仓的差额；单成员以及 `execution=independent` 仍由各策略自己下单。
`portfolio_backtest.py` 启动共用账户回测；共享预算通过清单显式配置，只限制新开仓规模，不代替净额。
组合层可以导入具体策略，不包含交易信号规则；具体策略不反向导入组合层。
组合使用说明及未来实盘入口设计见 [`PORTFOLIO.md`](PORTFOLIO.md)。

## 目录结构

```text
strategy_python/
├── _portfolio/               # 运行清单：成员、参数、启停、风险权重、账户
├── portfolio.py               # 注册与校验、回测及未来实盘复用的策略组装
├── portfolio_backtest.py      # 组合回测启动入口
├── PORTFOLIO.md               # 组合使用说明与实盘入口设计
├── _backtesting.py            # 回测环境组装，不导入具体策略
├── _report/                  # 报告生成代码；产物位于 data/reports/backtest/
├── _indicator/               # 至少被两个策略使用的指标
├── _model/                   # Signal、PositionTarget 等共享领域值对象
├── _risk/                     # 至少被两个策略使用的风险覆盖层
├── _execution/                # 至少被两个策略使用的执行适配器
└── <strategy_name>/
    ├── __init__.py
    ├── README.md              # 交易规则、参数、运行命令和结果位置
    ├── presets.py             # 可选：独立实验参数组合
    ├── config.py              # 静态 typed StrategyConfig
    ├── state.py               # 可选：运行期可变状态
    ├── indicators.py          # 可选：策略专属指标或特征
    ├── signal.py              # Alpha 信号，不接触账户和订单
    ├── position.py            # Signal → PositionTarget
    ├── risk.py                # 可选：策略专属风险限制
    ├── sizing.py              # 可选：策略专属定仓
    ├── execution.py           # 可选：策略专属订单执行
    ├── strategy.py            # Nautilus 生命周期、事件路由、模块编排
    └── backtest.py            # 当前项目约定的独立回测入口
```

报告产物位于项目根目录下的 `data/reports/backtest/<策略>/<run_id>/`，
组合报告位于 `data/reports/backtest/portfolio/<清单名称>/` 下的独立运行目录。

测试集中在项目根目录的 `tests/strategy_python/`，按相同的策略或共享组件名称分组。
测试文件按关键行为组织，可覆盖多个实现文件。

一个策略目录以 5～10 个平铺 Python 文件为宜。只有超过约 15～20 个文件且已经形成稳定
子领域时才增加下一层目录。

## 强制职责边界

1. `strategy.py` 只负责 Nautilus 生命周期、事件路由和模块编排。业务规则应进入对应
   领域模块。
2. `config.py` 只保存运行期间不会变化的 typed 配置；不得保存持仓、订单或指标状态。
3. `state.py` 只保存运行期状态；不得复制配置默认值或调用 Nautilus 下单 API。
4. `indicators.py` 只计算指标和特征，不判断账户资金，不创建订单。
5. `signal.py` 只回答方向、强度、置信度和原因；不得访问订单、账户、交易所或杠杆。
6. `position.py` 把 Signal 转换为目标仓位，优先表达“最终希望持有多少”，而不是
   `BUY/SELL` 指令。
7. `risk.py` 只能允许、削减、拒绝目标或触发强制退出，不得产生 Alpha 信号。
8. `execution.py` 只把最终目标转换成订单，不得判断 BOLL、MA、趋势等策略条件。
9. 回测和实盘必须实例化同一个 Strategy 类；Strategy 不得通过 `is_backtest` 分叉交易
   规则。

## 依赖方向

```text
backtest.py ──→ strategy.py
                    │
                    ├──→ config.py / state.py
                    ├──→ indicators.py → signal.py → position.py
                    └──→ risk.py → execution.py

具体策略 ──→ _indicator / _model / _risk / _execution
共享模块  ──✗ 任何具体策略
```

同时禁止：

- `signal.py` 导入 `execution.py` 或调用 `submit_order()`；
- `execution.py` 判断指标或产生交易信号；
- `risk.py` 决定做多或做空；
- `state.py` 包含参数默认值；
- 一个具体策略直接导入另一个具体策略；
- `strategy.py` 或领域模块导入自己的 `backtest.py`；
- `__init__.py` 执行下载、回测或其他副作用。

## 共享代码准入

模块只有被至少两个策略实际使用、语义稳定且关键行为有测试覆盖后，才能进入以下内部共享目录：

- `_indicator/`：通用指标和特征；
- `_model/`：不可变领域值对象；
- `_risk/`：通用风险覆盖层；
- `_execution/`：通用订单执行适配器。

只服务于单个策略的代码必须先留在策略目录。下划线前缀表示这些目录是项目内部实现，
不作为稳定公共 API。

## 文件和测试命名

- 策略目录使用小写 `snake_case`。
- 内部辅助目录优先使用简洁的单数或不可数名词，如 `_portfolio`、`_model`、`_indicator`、`_risk`。
- 具体策略主类所在文件固定命名 `strategy.py`，单策略回测入口固定命名 `backtest.py`。
- 组合入口直接位于 `strategy_python/`：`portfolio.py` 负责组装，`portfolio_backtest.py` 负责回测。
- 测试文件统一使用 `*_test.py`，不得改用 `test_*.py`。
- 测试统一放在项目根目录的 `tests/`，按被测模块的目录结构分组：
  数据管线位于 `tests/pipeline/`，策略与共享组件位于 `tests/strategy_python/`。
- pytest 使用 `importlib` 导入模式，允许不同策略目录保留同名测试文件。
  测试间的辅助函数引用使用 `tests.` 前缀，业务模块不导入测试包。
- 测试按关键行为和风险组织，不要求每个 Python 文件都有同名测试文件。
- 简短且相关的用例合并到已有行为测试中，例如信号与退出状态放在 `signal_test.py`，
  定仓、策略组装与回测入口放在 `strategy_test.py`；只有内容足够独立时再拆分。
- 必须保留信号边界、指标与 K 线计算、仓位与风险限制、订单生命周期，以及完整回测
  和报告正确性的覆盖。纯常量、简单数据结构、模块导出和薄封装不单独配测试，
  由使用它们的行为测试覆盖；影响交易规则的默认参数仍可在相关行为测试中检查。
- 删除用例前确认其行为已被其他用例覆盖，或仅重复实现细节；不以减少文件或用例
  数量为由删除独有的关键场景。

## 新策略如何复用回测入口

公共 `_backtesting.py` 已提供 `StrategyBacktest` 和 `run_backtest_cli`。单策略入口只需
声明名称、`BacktestSpec` 和策略工厂，不再复制数据检查、周期切换、参数传递或报告路径
逻辑。已有四个单策略入口均采用这一方式。

准备好 `BACKTEST_SPEC` 和 `create_strategy` 后，入口写法如下：

```python
from strategy_python._backtesting import (
    StrategyBacktest,
    build_argument_parser,
    run_backtest_cli,
)

BACKTEST = StrategyBacktest("my_strategy", BACKTEST_SPEC, create_strategy)
run_backtest = BACKTEST.run


def main():
    parser = build_argument_parser("回测我的策略")
    run_backtest_cli(run_backtest, parser)


if __name__ == "__main__":
    main()
```

策略工厂接收 `create_strategy(start, *, bar_type, ...)`，使用传入的 `bar_type` 创建
策略配置，每次返回一个新策略实例。`BacktestSpec` 保存账户、默认输入周期和预热等设置。
运行时 `--interval` 会同时更新回测读取周期和传给工厂的 BarType，不改变注册时的默认值。
输入是否能聚合策略信号周期，仍由策略初始化校验。

入口自动支持 `--start`、`--end`、`--interval`、`--check-data`、
`--refresh-archives` 和 `--force`。默认只读取本地数据，`--check-data` 才开启检查和补齐；`--force` 和
`--refresh-archives` 需配合 `--check-data`。这些名称对应公共运行参数，策略特有参数不要重用。
新增特有参数时在本策略的 parser 中声明，`dest` 与工厂形参同名即可。
默认日期也可以用
`parser.set_defaults(start=..., end=...)` 设置。组合回测复用同一命令行函数和底层引擎，
成员组装、共享预算与配置快照仍由组合入口负责。

Python 调用继续使用 `run_backtest(start, end, interval="1m", check_data=False)`，
成功时返回报告目录 `Path`，整个区间缺失且关闭检查时返回 `None`。
Pipeline 的文件复用、缺失补齐和强制重建逻辑仍只在 `pipeline/` 维护。

## 运行和产物

```bash
uv run python -m strategy_python.<strategy_name>.backtest
```

行情数据统一写入项目根目录的 `data/`，日志写入 `logs/`。持久化回测报表写入
`strategy_python/_report/backtest/<strategy_name>/`；组合报告写入
`strategy_python/_report/backtest/portfolio/<portfolio_name>/`。策略目录不再保存报告。
`_report` 中的生成代码和模板保留版本管理，产物集中在 `backtest/` 子目录并由 `.gitignore`
忽略。公共回测入口会打印并自动保存基础报表。

每次运行建立独立的 `<run_id>/` 子目录（UTC 时间加随机标识），保存 `parameters.json`
参数快照与回测区间、`summary.json` 引擎汇总，以及 `account.csv`、`fills.csv`、
`positions.csv` 明细，避免不同参数或重复运行相互覆盖。汇总中的非有限统计值保存为
JSON `null`；`summary.json` 最后写入，作为报表导出完成标志。
既有报表可保留原文件名，无需为调整目录重新运行回测。

回测完成后打开结果目录中的 `report.html` 查看中文交互报告，无需联网。
报告包含常规统计、参数、账户/成交/持仓明细，以及账户已实现净值曲线（账户余额 ÷ 初始余额，不含持仓浮盈亏）。主图的净收益、余额和回撤均按账户记录计算。
曲线按完整持仓平仓时间计入含结算币种手续费的已实现收益；分批退出延后至全部平仓时计入，
未平仓持仓与浮盈亏不计入。支持悬停、图例开关、缩放、拖动、自选日期和“全部 / 近 6 个月”。
“近 6 个月”相对回测结束时间计算，筛选保留原始累计值，指标卡统计完整回测区间。
