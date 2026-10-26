"""MA20 做多趋势跟随策略的静态配置。

参数来源约定：计算公式在领域模块和本目录 PARAMETER_SOURCES.md。
[筛选] 表示有历史候选记录，不等于统计定律；[经验] 表示规则可解释但精确值未校准。
[预算] 是风险/成本假设；[工程] 是运行约定。迁移时不得把时间换算当成统计来源。
"""

from nautilus_trader.model import BarType, InstrumentId

from strategy_python._config import PositiveFloat, PositiveInt, StrategyConfig
from strategy_python.btc_scaled_trend_following_long_optimized_5min.scale import BTC_PRICE_SCALE


class BtcScaledTrendFollowingLongOptimized5MinConfig(StrategyConfig, frozen=True, kw_only=True):
    # [工程] 合约定义来自目标市场；本轮默认BTC永续。
    instrument_id: InstrumentId
    # [工程] 输入周期与合约须一致；内部信号周期另定。
    bar_type: BarType
    # [预算] 本方向策略最多独立持仓份数1
    max_positions_per_side: PositiveInt = 1
    # [经验] 仅多份持仓时限制均价间距；默认1份不生效。
    additional_entry_spacing: PositiveFloat = (
        0.04 * BTC_PRICE_SCALE
    )  # BTC波动尺度；上方注释数值为ETH来源值
    # [工程] 订单隔离标签，不是统计参数。
    order_id_tag: str = "TFL"
    # [工程] 工厂写入开始时间；预热数据不交易。
    trade_start_ns: int = 0
    # [经验] 趋势：5分钟MA20；PBS：小时SMA20
    ma_period: PositiveInt = 20
    # [经验] 交叉模式不用此突破价，但仍要求偏离模型预热完成。
    entry_ma_period: PositiveInt = 99
    # [经验] ma_cross采用均线交叉市价入场；breakout为可选突破分支
    entry_trigger: str = "ma_cross"
    # [经验] 5分钟快SMA样本数；52的原始校准依据未找到。
    cross_fast_period: PositiveInt = 52
    # [经验] 5分钟慢SMA样本数；99的原始校准依据未找到。
    cross_slow_period: PositiveInt = 99
    # [经验] 历史最大偏离回溯长度；交叉模式仍影响预热。
    deviation_lookback: PositiveInt = 52
    # [经验] 小时均线20根=20小时
    trend_ma_period: PositiveInt = 20
    # [经验] 比较当前单步MA斜率与6小时前斜率。
    trend_slope_lookback: PositiveInt = 6
    # [经验] True比较小时MA斜率的变化，不是只要求斜率正负。
    entry_on_slope_change: bool = True
    # [经验] 小时MA逆向变动上限；空头默认None不限制。
    max_adverse_hourly_ma_return: float | None = (
        0.001 * BTC_PRICE_SCALE
    )  # BTC波动尺度；上方注释数值为ETH来源值
    # [预算] 名义金额=盯市权益×fraction；数量再按合约步长向下取整。
    equity_fraction_per_side: PositiveFloat = 1.0
    # [经验] True使用峰值浮盈保护；False切换可选MA退出
    peak_profit_exit: bool = True
    # [经验] 保护线=entry+(peak-entry)×lock；65%为经验锁利比例。
    peak_profit_lock: float = 0.65
    # [经验] 默认关闭；动态函数的端点也没有统计校准依据。
    dynamic_peak_profit_lock: bool = False
    # [经验] 可选MA退出分支中按反向交叉退出；默认False
    exit_on_reverse_cross: bool = False
    # [经验] 可选用专门MA退出线；默认False
    exit_on_ma99: bool = False
    # [经验] 专门退出均线周期99；对应开关默认关闭
    exit_ma_period: PositiveInt = 99
    # [经验] MA退出首线沿持仓方向偏离均线0.2σ
    first_exit_std: float = 0.2
    # [经验] MA退出末线沿持仓反方向偏离均线0.3σ
    final_exit_std: float = 0.3
    # [预算] 相对实际入场价的初始止损距离；不是权益亏损率。
    max_loss_return: float | None = 0.005 * BTC_PRICE_SCALE  # BTC波动尺度；上方注释数值为ETH来源值
    # [工程] 挂原生止损市价单，价格仍由入场价和距离计算。
    native_stop_loss: bool = True
    # [筛选] 峰值毛浮盈/入场价达门槛才锁利；不乘杠杆、不扣费。
    take_profit_activation_return: float | None = (
        0.03 * BTC_PRICE_SCALE
    )  # BTC波动尺度；上方注释数值为ETH来源值
    # [经验] 可选按MA斜率反向退出；默认False
    exit_on_ma_slope_reversal: bool = False
    # [经验] 可选按标准化斜率收紧退出；默认False
    tighten_exits_with_slope_risk: bool = False
    # [经验] 标准化斜率EMA周期1，alpha=2/(1+1)=1
    slope_risk_ema_period: PositiveInt = 1
    # [经验] 斜率风险映射尺度0.1：clip(0.5-顺向标准化斜率/(2×scale),0,1)
    slope_risk_scale: PositiveFloat = 0.1
    # [经验] fixed规则最多收紧1.5σ再乘风险/方向倍率
    slope_risk_max_tightening_std: float = 1.5
    # [经验] fixed按标准差定移位；distance按距离现价的可用空间
    slope_risk_rule: str = "fixed"
    # [经验] distance规则保留现价0.1σ缓冲
    slope_risk_price_buffer_std: float = 0.1
    # [经验] 多头斜率收紧倍率1.0
    slope_risk_long_multiplier: float = 1.0
    # [经验] 可选随正浮盈降低额外收紧强度；默认False
    profit_relaxes_slope_risk: bool = False
    # [经验] 放松系数1/(1+正浮盈/0.01)的尺度
    profit_relaxation_scale: PositiveFloat = (
        0.01 * BTC_PRICE_SCALE
    )  # BTC波动尺度；上方注释数值为ETH来源值
    # [经验] 多头可选MA退出首线触及即全平开关；默认True
    long_full_exit_on_first: bool = True
    # [经验] 平仓后固定K线冷却根数0
    cooldown_bars: int = 0
    # [经验] 止损后冷却秒数0
    stop_loss_cooldown_seconds: int = 0
    # [经验] 回到5分钟MA20±0.2σ才重新获入场资格
    rearm_std_multiplier: PositiveFloat = 0.2
    # [工程] 停止清仓开关；不是持仓时间限制。
    close_positions_on_stop: bool = True
