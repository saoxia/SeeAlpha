# BTC 波动尺度适配版

已为四类策略的原版与优化版生成 **8 个独立的 `btc_scaled_*` 策略**。现有 `btc_*` 固定参数版本保留，用于对照。

## 换算依据

使用 UTC `[2020-01-01, 2021-01-01)` 的 BTC、ETH 1分钟收盘数据，取两者时间戳一致、每日1440根且连续的交易日。360天有效，6天因数据不完整排除。

每个有效日，分别计算日内1439个相邻分钟的对数收益，得到 `RV = sqrt(sum(log(close_t / close_(t-1))²))`。不跨日连接，也不填补缺失数据。

统一系数 `s = median(RV_BTC,d / RV_ETH,d) = 0.736303407400`。

所有纳入换算的价格收益阈值使用 `BTC阈值 = ETH来源阈值 × s`，代码保留完整浮点精度，实际订单仍按BTC合约价格步长处理。日比值的25%与75%分位数分别为 0.599705、0.911746，说明这个常数是历史典型尺度，并不代表每天都相同。

**这是新增的适配假设：不同持仓时长的价格距离统一按典型分钟波动比缩放。它保持相对波动倍数和原有距离比例，不保证保持触发概率、尾部风险或持仓时长。** 尤其均值回归25%硬止损属于较大尾部距离，尚未验证极端跌幅是否也服从同一比例。没有按BTC收益挑选系数，也没有声称恢复出原始ETH参数的统计来源。

## 主要变化

| 参数 | 来源尺度 | BTC尺度 |
|---|---:|---:|
| 趋势初始止损、均值回归全平止盈 | 0.5% | 0.368152% |
| 均值回归加仓距离，第2/3/4层，相对此前均价 | 0.5% / 1% / 2% | 0.368152% / 0.736303% / 1.472607% |
| 均值回归硬止损距离 | 25% | 18.407585% |
| 趋势原版止盈启动 | 2% | 1.472607% |
| 趋势多头优化版止盈启动 | 3% | 2.208910% |
| 趋势空头优化版止盈启动 | 4% | 2.945214% |
| 趋势多头小时MA逆向变动上限 | 0.1% | 0.073630% |
| 小时反弹空头允许止损距离 | 0.4%—3% | 0.294521%—2.208910% |

可选分支中的多仓间距、浮盈放松尺度，以及默认关闭的动态锁利曲线横轴端点也一起换算；动态锁利的40%—95%输出比例和曲线形状不变。原本为None的限制仍关闭。传入自定义参数时必须直接使用BTC尺度值，工厂不会重复乘系数。

BOLL标准差倍数、ATR缓冲倍数、均线周期、观察窗口、锁利65%、4R止盈、仓位层数、资金预算、名义金额上限及真实/预估费率不变。MRL强度和双谷风险曲线、恢复时长的经验参数也不变：没有依据用波动比重估这些维度。

止盈值仍表示毛价格收益，费用单独保留，**没有保证费后收益倍数相等**。MRL在超过2层时的均价减半退出也仍可能亏手续费。缩短价格止损距离会使MRL/PBS按原风险预算算出的数量增加，预算不变不代表持仓数量或真实回撤不变。PBS两个版本继续分别保留0.5%/1倍和1.5%/3倍预算，不能把它们的直接收益差视为信号改进。

## 使用入口

| 策略 | 独立目录 |
|---|---|
| MRL_original | [btc_scaled_mean_reversion_long_5min](btc_scaled_mean_reversion_long_5min/README.md) |
| MRL_optimized | [btc_scaled_mean_reversion_long_optimized_5min](btc_scaled_mean_reversion_long_optimized_5min/README.md) |
| TFL_original | [btc_scaled_trend_following_long_5min](btc_scaled_trend_following_long_5min/README.md) |
| TFL_optimized | [btc_scaled_trend_following_long_optimized_5min](btc_scaled_trend_following_long_optimized_5min/README.md) |
| TFS_original | [btc_scaled_trend_following_short_5min](btc_scaled_trend_following_short_5min/README.md) |
| TFS_optimized | [btc_scaled_trend_following_short_optimized_5min](btc_scaled_trend_following_short_optimized_5min/README.md) |
| PBS_original | [btc_scaled_pullback_short_1h](btc_scaled_pullback_short_1h/README.md) |
| PBS_optimized | [btc_scaled_pullback_short_optimized_1h](btc_scaled_pullback_short_optimized_1h/README.md) |

默认交易起点2021-01-01 UTC。策略构造时拒绝更早的 `trade_start_ns`；预热可读取2020年数据，但不应交易。无日期参数的独立工厂也采用2021年起点。当前不做每日滚动更新，回测过程中不重新校准系数。

短区间独立运行示例：

```bash
.venv/bin/python -m strategy_python.btc_scaled_trend_following_long_optimized_5min.backtest --start 2022-05-01 --end 2022-06-01
```

全历史采用连续分批加载入口，避免一次性加载全部分钟数据：

```bash
.venv/bin/python -m strategy_python._optimization.btc_scale_20260915.run TFL_optimized full
```

## 证据与验证范围

**完整回测现已完成：** [18项回测、固定尺度对照及资金曲线](_optimization/btc_scale_20260915/RESULTS.md)。

- [校准统计和输入文件哈希](_optimization/btc_scale_20260915/calibration.json)
- [逐日波动统计](_optimization/btc_scale_20260915/daily_volatility.csv)
- [40项数值换算清单，含默认关闭分支](_optimization/btc_scale_20260915/parameter_changes.json)
- [验证记录](_optimization/btc_scale_20260915/VERIFICATION.md)
- [原固定参数版完整回测](_optimization/btc_transfer_20260914/RESULTS.md)

2020年统计没有用到2021年之后的价格，但原始ETH规则曾使用后续历史筛选，BTC历史结果也已经查看过。因此这不是整体研究流程的全新盲测。完整历史适配版比较已完成：8个默认版本全历史均亏损，5个净收益改善，仅2个同时降低回撤。不能据此排除过拟合，详见完整报告。
