# BTC两个多头：完整历史复核

趋势跟随多头、2σ均值回归多头各运行原费率和每次成交额外5bp两档成本，共4次原生连续回放。
保持参数与风险预算，检查补齐开仓约束后的真实交易路径，不搜索新参数。

[中文报告](RESULTS.md) · [图表报告](report.html) · [事先冻结的协议](PROTOCOL.md)

## 本地复核

在项目根目录使用现有环境与行情数据：

```bash
.venv/bin/python -m strategy_python._optimization.btc_long_validation_20260921.run verify
.venv/bin/python -m strategy_python._optimization.btc_long_validation_20260921.run all
MPLCONFIGDIR=/tmp/nautilus-mpl .venv/bin/python -m strategy_python._optimization.btc_long_validation_20260921.report
```

`all`最多同时执行4项。仅当冻结输入、参数与已保存输出校验值一致时复用完成结果；不下载新行情。
各实验的配置、成交、持仓、账户、日末权益、年度指标、完整持仓现金流和完成校验位于 `runs/`。

`manifest.json`保存53个源码/协议文件、2435个数据文件、8个旧对照文件的哈希，以及运行环境和配置比较。
`frozen_sources.zip`包含冻结源码与协议，`archive.json`记录归档哈希；不包含行情数据。
报告生成程序另存于 `report_source.zip`，其哈希和最终检查记录见 `final_validation.json`。
后续代码或数据改变时，应在独立副本恢复冻结输入后复核，不覆盖后续工作。

所有收益都是已经参与研发的历史诊断，不是未来时间验证。原报告及修复前结果保持原样；
本目录单独保存修复后的回放，不把固定曲线扣费等同于成本改变后重新定仓。
