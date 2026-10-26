# btc_scaled_pullback_short_optimized_1h

BTC波动尺度适配版，对应 btc_pullback_short_optimized_1h。

[换算公式、限制及参数表](../BTC_SCALE.md) · [参数来源](PARAMETER_SOURCES.md)

独立入口：`python -m strategy_python.btc_scaled_pullback_short_optimized_1h.backtest --start 2022-05-01 --end 2022-06-01`。

默认交易起点2021-01-01 UTC；更早起点会拒绝，避免使用尚未完成的2020年校准统计。调用配置时必须设置 trade_start_ns。外部传入覆盖值按BTC单位解释，不会再次换算。
