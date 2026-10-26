# 项目测试

测试集中存放，按业务模块分组，不要求与每个实现文件一一对应。

```text
tests/
├── pipeline/                          # 下载、校验、转换和数据准备
├── forward_validation/                # 因果时序、恢复、健康、样本统计、备份与冻结评估
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

本机使用 `bash handover/check_dev.sh` 或 `.venv/bin/python -m ops.check_project --development`，一次运行源码校验、代码检查和全量测试，记录完整日志；不触发依赖更新。默认 `ops.check_project` 还检查未迁移的前向冻结数据。

`tests/forward_validation/operations_test.py`覆盖不读取收益的样本统计、先去重后检查缺失、备份原始证据损坏、独立恢复、到期前禁止评估、断网到期仍报告不足、完成幂等、持续故障提醒和重启限制。所有到期场景使用独立测试数据与测试时钟，不修改真实研究的时间或结果。

代码检查排除历史研究目录 `_optimization/` 和报告历史 `analysis/`、`backtest/`，保留快照原始字节。现行报告生成器、管线、全部策略、运维和测试仍在检查范围内。21 个已有策略文件的导入排序或公共导出提示按精确文件登记在 `ops/lint_baseline.json`，不批量改动冻结实现；项目检查同时验证这些文件的内容哈希，源码变化须重新审查例外。测试文件本轮的样式问题已修复。
`tests/__init__.py` 固定本项目测试包的来源，测试间引用辅助函数时使用 `tests.` 前缀。
完整测试约定见 [Python 策略工程约定](../strategy_python/README.md#文件和测试命名)。
