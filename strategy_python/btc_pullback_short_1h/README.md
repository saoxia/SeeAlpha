# btc_pullback_short_1h

BTC-USDT永续独立策略：`BtcPullbackShort1hStrategy`，源策略为[pullback_short_1h](../pullback_short_1h/README.md)。领域模块全部独立，不导入源策略。

[参数来源](PARAMETER_SOURCES.md) · [BTC迁移回测结果](../_optimization/btc_transfer_20260914/RESULTS.md)

保留源策略默认值；初始资金500 USDT、账户保证金杠杆5倍、1分钟输入、30天预热。
默认评估区间为UTC `[2021-01-01,2026-08-31)`，即截至2026年8月30日。只读取本地已准备行情。

```bash
.venv/bin/python -m strategy_python.btc_pullback_short_1h.backtest --start 2025-01-01 --end 2026-08-31
```

全历史批量结果采用分批加载以限制内存，交易状态连续。原版与优化版均未根据BTC结果再选参。
