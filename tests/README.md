# 项目测试

测试集中存放，按业务模块分组，不要求与每个实现文件一一对应。

```text
tests/
├── pipeline/                          # 下载、校验、转换和数据准备
├── connectors/                        # 原生交易所适配器接入（实现位于 engine/connectors）
├── feed/                              # 连续行情、补洞、回放与就绪（实现位于 engine/feed）
├── engine/                            # 常驻引擎的事实、账户范围和 API 边界
├── web/                               # 任务执行与 HTTP 访问边界
├── core/                              # 公共基础能力与开发环境
├── baselines/                         # 当前源码基线和授权更新清单
└── strategy_python/
    ├── _backtesting_test.py            # 公共回测与产物导出
    ├── architecture_test.py            # 业务代码依赖边界
    ├── portfolio_test.py               # 组合配置和策略组装
    ├── portfolio_backtest_test.py      # 共用账户回测与风险预算
    ├── _report/                       # 报告正确性
    ├── _risk/                         # 共享风险额度
    ├── mean_reversion_long_5min/
    ├── mean_reversion_short_5min/
    ├── trend_following_long_5min/
    └── trend_following_short_5min/
```

在项目根目录运行：

```bash
uv run pytest
uv run pytest tests/pipeline
uv run pytest tests/strategy_python/mean_reversion_long_5min
```

测试文件保持 `*_test.py` 命名；pytest 的 `importlib` 模式避免不同目录的同名文件冲突。

`python -m pytest -q` 运行全量测试，包含 `source_baselines_test.py` 的历史来源核验；
`python -m ruff check .` 独立检查代码规范。Web 管理端可顺序运行两者并保存报告。


代码检查排除历史研究目录 `_optimization/` 和报告历史 `analysis/`，保留快照原始字节。现行报告生成器、管线、全部策略、运维和测试仍在检查范围内。`tests/baselines/lint_baseline.json` 当前没有逐文件样式例外；历史研究来源仍由源码基线测试核验，已授权的源码变更登记在 `tests/baselines/source_updates.json`，历史归档保留原始字节。
`tests/__init__.py` 固定本项目测试包的来源，测试间引用辅助函数时使用 `tests.` 前缀。
完整测试约定见 [Python 策略工程约定](../strategy_python/README.md#文件和测试命名)。
