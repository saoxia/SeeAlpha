# 趋势空头参数稳定域研究

固定BTC趋势空头候选，在BTC/ETH执行相同参数邻域和成本压力，共106次连续历史回测。
实验设计、停止条件和限制见 [PROTOCOL.md](PROTOCOL.md)。
正式汇总写入 `RESULTS.md`、`report.html`、`comparison.csv`、`decision.json`；
只有106项全部完成且冻结输入校验通过后，报告程序才会生成汇总。

## 本地复核

在项目根目录、使用本项目现有环境和数据运行：

```bash
.venv/bin/python -m strategy_python._optimization.tfs_stability_20260921.study verify
.venv/bin/python -m strategy_python._optimization.tfs_stability_20260921.run_all --workers 8
MPLCONFIGDIR=/tmp/nautilus-mpl .venv/bin/python -m strategy_python._optimization.tfs_stability_20260921.report
```

批量执行会复用参数和冻结输入一致的已完成实验；不会下载新数据。
每项输出位于 `runs/<实验ID>/`。`progress.json` 记录完成项与失败项；
`execution_audit.json` 汇总成交现金流、价格步长、缺失分钟和开仓最低金额核验。

## 冻结与历史版本

[manifest.json](manifest.json)记录51个研究输入文件与4878个数据文件的SHA256。
[frozen_sources.zip](frozen_sources.zip)保存与清单逐字节一致的研究源码和协议；
[frozen_sources.json](frozen_sources.json)记录压缩包校验值。压缩包不含行情数据。
[runtime.json](runtime.json)记录本轮Python、依赖版本及关键引擎文件校验值；运行期间未升级环境。
如果当前代码已改变，应在独立副本中恢复该归档再复核，不应覆盖后续研究。

`before_constraints/`保留发现开仓下限问题之前的源码、45次完成结果和原中心复现记录。
当前106次运行执行修正后的开仓约束，不与旧结果混合。
所有时间区间都已被研究使用，报告属于历史诊断，不能重新称为盲测。
