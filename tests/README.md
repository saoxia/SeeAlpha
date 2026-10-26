# 项目测试

测试集中存放，按业务模块分组，不要求与每个实现文件一一对应。

```text
tests/
├── pipeline/                          # 下载、校验、转换和数据准备
├── connectors/                        # 原生交易所适配器接入
├── feed/                              # 连续行情、补洞、回放与就绪
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


代码检查排除历史研究目录 `_optimization/` 和报告历史 `analysis/`，保留快照原始字节。现行报告生成器、管线、全部策略、运维和测试仍在检查范围内。21 个已有策略文件的导入排序或公共导出提示按精确文件登记在 `tests/baselines/lint_baseline.json`，不批量改动冻结实现；源码基线测试同时验证这些文件的内容哈希，源码变化须重新审查例外。测试文件本轮的样式问题已修复。
`tests/__init__.py` 固定本项目测试包的来源，测试间引用辅助函数时使用 `tests.` 前缀。
完整测试约定见 [Python 策略工程约定](../strategy_python/README.md#文件和测试命名)。
