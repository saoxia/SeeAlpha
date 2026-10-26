"""ETHUSDT v4 均值回归做多策略的静态配置。

参数来源约定：计算公式在领域模块和本目录 PARAMETER_SOURCES.md。
[筛选] 表示有历史候选记录，不等于统计定律；[经验] 表示规则可解释但精确值未校准。
[预算] 是风险/成本假设；[工程] 是运行约定。迁移时不得把时间换算当成统计来源。
"""

from nautilus_trader.config import PositiveFloat, PositiveInt, StrategyConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId


class MechanismMeanReversionLong5MinConfig(StrategyConfig, frozen=True):
    """静态配置；交接值、本轮筛选值与风险预算分别标注来源。"""

    # [工程] 合约定义来自目标市场；本轮默认ETH永续。
    instrument_id: InstrumentId
    # [工程] 输入周期与合约须一致；内部信号周期另定。
    bar_type: BarType
    # [工程] 工厂写入开始时间；预热数据不交易。
    trade_start_ns: int = 0
    # [预算] 最多4个等数量仓位单元
    max_units: PositiveInt = 4
    # [经验] 5分钟收盘样本数；20=100分钟，时间换算不是选优依据。
    bollinger_period: PositiveInt = 20
    # [经验] 下轨=SMA-k×总体标准差；k是阈值，不是固定尾概率。
    bollinger_std_multiplier: PositiveFloat = 2.0
    # [经验] 15分钟SMA样本数99；没有原始周期估计记录。
    ma_filter_period: PositiveInt = 99
    # [经验] 最近54次low<当根MA的状态数；不是独立样本数。
    ma_filter_window: PositiveInt = 54
    # [经验] 不利状态次数/窗口数的上限；默认最多35/54次。
    ma_reentry_ratio: PositiveFloat = 0.65
    # [经验] ≤2单元在均价上方0.5%全平；>2单元先在均价减半
    take_profit_rate: PositiveFloat = 0.005
    # [经验] MRL仅限制加仓窗口；PBS才是持仓时间退出。
    max_holding_hours: PositiveInt = 48
    # [经验] MRL止盈全平后等待5分钟
    flat_cooldown_minutes: PositiveInt = 5
    # [预算] 止损=当前均价×(1-distance)；25%是价格距离。
    hard_stop_distance: PositiveFloat = 0.25
    # [预算] 权益预算再乘强度/位置/恢复系数，不是实际损失保证。
    cycle_risk_fraction: PositiveFloat = 0.30
    # [经验] 方向最大跌幅/区间位置回溯312小时；不是已识别市场周期。
    risk_move_hours: PositiveInt = 312
    # [经验] 参考最大跌幅窗口，向前错开move_hours；不是分位数。
    risk_reference_days: PositiveInt = 120
    # [预算] 定仓用入场费率假设；实际扣费由合约/成交决定。
    entry_fee_rate: float = 0.0002
    # [预算] 定仓用强退费率假设；迁移时核对市场成本。
    forced_exit_fee_rate: float = 0.0005
    # [预算] 计划止损损失和费用合计乘(1+buffer)。
    stop_cost_buffer: float = 0.05
    # [经验] 止损后6小时及日线MA52恢复检查
    recovery_ma_period: PositiveInt = 52
    # [工程] 仅保存6小时K线快照；当前不进入交易决策。
    recovery_snapshot_bars: PositiveInt = 26
    # [经验] 恢复释放等待d≥7天才检查额外风险窗口。
    daily_risk_minimum_release_days: PositiveFloat = 7.0
    # [经验] 风险窗口截距26天；未找到事件拟合依据。
    daily_risk_base_days: PositiveFloat = 26.0
    # [经验] 窗口下界=base_days+lower_slope×释放等待天数。
    daily_risk_lower_slope: PositiveFloat = 1.5
    # [经验] 窗口上界=base_days+upper_slope×释放等待天数。
    daily_risk_upper_slope: PositiveFloat = 1.8
    # [经验] 窗口内风险乘此系数；不是已估计再跌概率。
    daily_risk_coefficient: PositiveFloat = 0.30
    # [工程] 停止清仓开关；不是持仓时间限制。
    close_positions_on_stop: bool = True

    # Conditional pre-recovery quantiles, in entry BOLL-gap units.
    mechanism_add_multipliers: tuple[float, float, float] = (0.3, 0.9, 2.3)
    mechanism_flat_risk: bool = False
    mechanism_flat_multiplier: float = 1.0
