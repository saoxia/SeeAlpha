"""下跌趋势反弹做空的静态配置。

参数来源约定：计算公式在领域模块和本目录 PARAMETER_SOURCES.md。
[筛选] 表示有历史候选记录，不等于统计定律；[经验] 表示规则可解释但精确值未校准。
[预算] 是风险/成本假设；[工程] 是运行约定。迁移时不得把时间换算当成统计来源。
"""

from nautilus_trader.model import BarType, InstrumentId

from strategy_python._config import PositiveFloat, PositiveInt, StrategyConfig


class MechanismPullbackShort1hConfig(StrategyConfig, frozen=True, kw_only=True):
    # [工程] 合约定义来自目标市场；本轮默认ETH永续。
    instrument_id: InstrumentId
    # [工程] 输入周期与合约须一致；内部信号周期另定。
    bar_type: BarType
    # [工程] 订单隔离标签，不是统计参数。
    order_id_tag: str = "PBS"
    # [工程] 工厂写入开始时间；预热数据不交易。
    trade_start_ns: int = 0
    # [筛选] 趋势：5分钟MA20；PBS：小时SMA20
    ma_period: PositiveInt = 20
    # [经验] 日线收盘样本数；TFS含当日动态值，PBS仅完整日线。
    daily_boll_period: PositiveInt = 20
    # [经验] BOLL总体标准差倍数；low穿轨不是收盘尾概率。
    daily_boll_std: PositiveFloat = 2.0
    # [经验] 小时TR的算术均值样本数；不是Wilder递推ATR。
    atr_period: PositiveInt = 14
    # [经验] 最近完整小时最高价窗口；止损结构随行情更新。
    swing_period: PositiveInt = 12
    # [筛选] 止盈=实际入场价-R×(结构止损-实际入场价)。
    reward_risk: PositiveFloat = 2.5
    # [筛选] MRL仅限制加仓窗口；PBS才是持仓时间退出。
    max_holding_hours: PositiveInt = 24
    # [预算] 计划损失/权益预算；旧类0.5%，旧组合/新类1.5%。
    risk_fraction: PositiveFloat = 0.005
    # [预算] 名义金额/权益上限；旧类1倍，旧组合/新类3倍。
    max_notional_fraction: PositiveFloat = 3.0
    # [预算] 止损距信号收盘的下限，避免过窄结构；未校准噪声分位数。
    min_stop_fraction: PositiveFloat = 0.004
    # [预算] 止损距信号收盘的上限；风险偏好，不是市场定律。
    max_stop_fraction: PositiveFloat = 0.03
    # [经验] 止损=结构最高价+此倍数×ATR；倍数未做分位数校准。
    stop_atr_buffer: PositiveFloat = 0.25
    # [预算] 每单位计划成本=(entry+stop)×buffer；并非真实扣费。
    fee_buffer: PositiveFloat = 0.0007
    # [工程] 停止清仓开关；不是持仓时间限制。
    close_positions_on_stop: bool = True

    mechanism_buffer_atr: float = 0.0
    mechanism_min_stop_atr: float = 1.0243847689576042
    mechanism_max_stop_atr: float = 2.539224209962621

    cycle_require_falling_ma: bool = False
