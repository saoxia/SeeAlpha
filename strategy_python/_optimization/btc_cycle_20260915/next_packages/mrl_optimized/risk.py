"""ETHUSDT v4 的方向风险预算和硬止损恢复规则。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from strategy_python._optimization.btc_cycle_20260915.next_packages.mrl_optimized.bars import (
    NANOSECONDS_PER_DAY,
    NANOSECONDS_PER_HOUR,
    CompletedBar,
)
from strategy_python._optimization.btc_cycle_20260915.next_packages.mrl_optimized.state import (
    DirectionState,
)


@dataclass(frozen=True)
class RiskParameters:
    move_hours: int = 312
    reference_days: int = 120
    # [经验形状] 强度是行情计算值，但下列强度→预算结点没有原始拟合证据。
    # 见PARAMETER_SOURCES.md与../PARAMETER_QUESTIONS.md的Q1～Q3。
    strength_knots: tuple[tuple[float, float], ...] = (
        (0.00, 0.75),
        (0.55, 0.75),
        (0.62, 0.80),
        (0.80, 0.90),
        (1.00, 1.05),
        (1.20, 1.20),
        (1.40, 1.30),
    )
    # [经验形状] 17%/83%双谷、34%～66%平顶并非由分位数生成；两端系数回到1。
    boundary_minimum: float = 0.10
    boundary_valley_left: float = 0.17
    boundary_safe_left: float = 0.34
    boundary_safe_right: float = 0.66
    boundary_valley_right: float = 0.83
    risk_fraction: float = 0.30
    max_units: int = 4
    # [经验] 相对此前均价的分层距离；实际价位计算，不复制ETH绝对价格。
    add_offsets: tuple[tuple[int, float], ...] = ((2, 0.005), (3, 0.01), (4, 0.02))
    stop_distance: float = 0.25
    entry_fee_rate: float = 0.0002
    forced_exit_fee_rate: float = 0.0005
    stop_cost_buffer: float = 0.05


class DirectionalRiskModel:
    """按方向波动、312 小时位置和完整四层止损成本反推数量。"""

    def __init__(self, parameters: RiskParameters) -> None:
        self.parameters = parameters
        maximum = parameters.reference_days * 24 + parameters.move_hours + 48
        self.hourly_bars: deque[tuple[int, float, float]] = deque(maxlen=maximum)
        self.up_anchor: float | None = None
        self.down_anchor: float | None = None
        self._last_anchor_day: int | None = None

    @staticmethod
    def _smooth_knot_value(value: float, knots: tuple[tuple[float, float], ...]) -> float:
        if value <= knots[0][0]:
            return knots[0][1]
        if value >= knots[-1][0]:
            return knots[-1][1]
        for index in range(1, len(knots)):
            right_x, right_y = knots[index]
            if value <= right_x:
                left_x, left_y = knots[index - 1]
                ratio = (value - left_x) / (right_x - left_x)
                # [数学构造] smoothstep使端点导数为0；3与2来自插值边界条件。
                # 这是曲线平滑的理由，不是上面经验结点有预测能力的证据。
                smooth = ratio * ratio * (3 - 2 * ratio)
                return left_y + (right_y - left_y) * smooth
        return knots[-1][1]

    def strength_coefficient(self, strength: float) -> float:
        return self._smooth_knot_value(strength, self.parameters.strength_knots)

    def boundary_coefficient(self, direction: int, position: float) -> float:
        """17%/83% 是二次 U 形谷底，34%--66% 精确保持 1。"""
        if direction != 1:
            raise ValueError("本策略仅允许做多风险定仓")
        config = self.parameters
        distance = position
        distance = min(1.0, max(0.0, distance))
        minimum = config.boundary_minimum
        amplitude = 1 - minimum
        if distance <= config.boundary_valley_left:
            normalized = (distance - config.boundary_valley_left) / config.boundary_valley_left
        elif distance < config.boundary_safe_left:
            normalized = (distance - config.boundary_valley_left) / (
                config.boundary_safe_left - config.boundary_valley_left
            )
        elif distance < config.boundary_safe_right:
            return 1.0
        elif distance < config.boundary_valley_right:
            normalized = (distance - config.boundary_valley_right) / (
                config.boundary_valley_right - config.boundary_safe_right
            )
        else:
            normalized = (distance - config.boundary_valley_right) / (
                1 - config.boundary_valley_right
            )
        return minimum + amplitude * normalized * normalized

    def _max_directional_moves(
        self,
        bars: list[tuple[int, float, float]],
        current_point: tuple[int, float] | None = None,
    ) -> tuple[float, float]:
        minimum_lows: deque[tuple[int, float]] = deque()
        maximum_highs: deque[tuple[int, float]] = deque()
        maximum_up = 0.0
        maximum_down = 0.0
        window_ns = self.parameters.move_hours * NANOSECONDS_PER_HOUR
        for point_time, high, low in bars:
            cutoff = point_time - window_ns
            while minimum_lows and minimum_lows[0][0] < cutoff:
                minimum_lows.popleft()
            while maximum_highs and maximum_highs[0][0] < cutoff:
                maximum_highs.popleft()
            if minimum_lows and minimum_lows[0][1] > 0:
                maximum_up = max(maximum_up, high / minimum_lows[0][1] - 1)
            if maximum_highs and maximum_highs[0][1] > 0:
                maximum_down = max(maximum_down, 1 - low / maximum_highs[0][1])
            while minimum_lows and minimum_lows[-1][1] >= low:
                minimum_lows.pop()
            minimum_lows.append((point_time, low))
            while maximum_highs and maximum_highs[-1][1] <= high:
                maximum_highs.pop()
            maximum_highs.append((point_time, high))
        if current_point is not None:
            point_time, price = current_point
            cutoff = point_time - window_ns
            while minimum_lows and minimum_lows[0][0] < cutoff:
                minimum_lows.popleft()
            while maximum_highs and maximum_highs[0][0] < cutoff:
                maximum_highs.popleft()
            if minimum_lows and minimum_lows[0][1] > 0:
                maximum_up = max(maximum_up, price / minimum_lows[0][1] - 1)
            if maximum_highs and maximum_highs[0][1] > 0:
                maximum_down = max(maximum_down, 1 - price / maximum_highs[0][1])
        return max(0.0, maximum_up), max(0.0, maximum_down)

    def on_hourly_bar(self, bar: CompletedBar) -> None:
        self.hourly_bars.append((bar.ts_end, bar.high, bar.low))
        current_day = bar.ts_end // NANOSECONDS_PER_DAY
        if current_day != self._last_anchor_day:
            self._last_anchor_day = current_day
            self._refresh_anchors(bar.ts_end)

    def _refresh_anchors(self, as_of_ns: int) -> None:
        # [来源/时序] 参考窗向前错开近期窗，避免同一近期跌幅同时充当参考极值。
        # 窗口312小时/120天仍是经验值；参考是最大方向跌幅，不是历史百分位。
        reference_end = as_of_ns - self.parameters.move_hours * NANOSECONDS_PER_HOUR
        reference_start = reference_end - self.parameters.reference_days * NANOSECONDS_PER_DAY
        bars = [item for item in self.hourly_bars if reference_start <= item[0] <= reference_end]
        if len(bars) < self.parameters.reference_days * 24 - 2:
            self.up_anchor = None
            self.down_anchor = None
            return
        up, down = self._max_directional_moves(bars)
        self.up_anchor = up if up > 0 else None
        self.down_anchor = down if down > 0 else None

    def cycle_context(
        self,
        direction: int,
        price: float,
        now_ns: int,
        recovery_coefficient: float = 1.0,
    ) -> dict[str, float] | None:
        if direction != 1:
            raise ValueError("本策略仅允许做多风险定仓")
        if price <= 0 or self.up_anchor is None or self.down_anchor is None:
            return None
        cutoff = now_ns - self.parameters.move_hours * NANOSECONDS_PER_HOUR
        bars = [item for item in self.hourly_bars if cutoff <= item[0] <= now_ns]
        if len(bars) < self.parameters.move_hours - 2:
            return None
        _recent_up, recent_down = self._max_directional_moves(
            bars,
            current_point=(now_ns, price),
        )
        range_high = max(max(item[1] for item in bars), price)
        range_low = min(min(item[2] for item in bars), price)
        if range_high <= range_low:
            return None
        # [计算来源] 在当时可见的近期高低范围中归一化当前价格，得到[0,1]位置。
        # 不同资产可用同一公式，但位置相同不保证未来损失分布相同。
        position = (price - range_low) / (range_high - range_low)
        recent_move = recent_down
        anchor = self.down_anchor
        base_fraction = self.parameters.risk_fraction
        if anchor is None or anchor <= 0:
            return None
        strength = recent_move / anchor
        strength_coefficient = self.strength_coefficient(strength)
        boundary_coefficient = self.boundary_coefficient(direction, position)
        recovery_coefficient = min(1.0, max(0.0, recovery_coefficient))
        return {
            "risk_fraction": (
                base_fraction * strength_coefficient * boundary_coefficient * recovery_coefficient
            ),
            "strength": strength,
            "strength_coefficient": strength_coefficient,
            "boundary_coefficient": boundary_coefficient,
            "recovery_coefficient": recovery_coefficient,
            "position": position,
        }

    def planned_entry_prices(self, direction: int, initial_price: float) -> list[float]:
        if direction != 1:
            raise ValueError("本策略仅允许做多风险定仓")
        prices = [initial_price]
        average = initial_price
        offsets = dict(self.parameters.add_offsets)
        for level in range(2, self.parameters.max_units + 1):
            price = average * (1 - direction * offsets[level])
            prices.append(price)
            average = sum(prices) / len(prices)
        return prices

    def size_initial_entry(
        self,
        direction: int,
        initial_price: float,
        now_ns: int,
        equity: Decimal,
        size_increment: Decimal,
        recovery_coefficient: float = 1.0,
    ) -> dict[str, Decimal | dict[str, float]] | None:
        context = self.cycle_context(
            direction,
            initial_price,
            now_ns,
            recovery_coefficient,
        )
        if context is None or equity <= 0 or size_increment <= 0:
            return None
        prices = self.planned_entry_prices(direction, initial_price)
        full_average = sum(prices) / len(prices)
        distance = self.parameters.stop_distance
        stop_price = full_average * (1 - direction * distance)
        gross_stop_loss = abs(stop_price - full_average) * self.parameters.max_units
        entry_fees = sum(prices) * self.parameters.entry_fee_rate
        forced_exit_fee = (
            stop_price * self.parameters.max_units * self.parameters.forced_exit_fee_rate
        )
        risk_per_quantity = Decimal(
            str(
                (gross_stop_loss + entry_fees + forced_exit_fee)
                * (1 + self.parameters.stop_cost_buffer)
            )
        )
        # [计算来源] q=权益风险预算/每层数量对应的满四层止损损失及费用缓冲。
        # 价格由本标的行情生成，数量再按本合约步长向下取整；预算系数仍为风险假设。
        available_risk = equity * Decimal(str(context["risk_fraction"]))
        raw_quantity = available_risk / risk_per_quantity
        quantity = (raw_quantity / size_increment).to_integral_value(
            rounding=ROUND_DOWN,
        ) * size_increment
        if quantity <= 0:
            return None
        return {
            "unit_quantity": quantity,
            "risk_per_quantity": risk_per_quantity,
            "planned_risk": quantity * risk_per_quantity,
            "risk_context": context,
        }

    def entry_preserves_cycle_risk(
        self,
        state: DirectionState,
        quantity: Decimal,
        price: Decimal,
    ) -> bool:
        if not state.invested or state.cycle_risk_budget <= 0:
            return True
        new_quantity = state.quantity + quantity
        new_average = (state.quantity * state.average_price + quantity * price) / new_quantity
        distance = Decimal(str(self.parameters.stop_distance))
        stop_price = new_average * (Decimal(1) - state.direction * distance)
        gross_loss = abs(stop_price - new_average) * new_quantity
        entry_fees = state.cycle_entry_fees + (
            quantity * price * Decimal(str(self.parameters.entry_fee_rate))
        )
        forced_fee = new_quantity * stop_price * Decimal(str(self.parameters.forced_exit_fee_rate))
        projected = (gross_loss + entry_fees + forced_fee) * Decimal(
            str(1 + self.parameters.stop_cost_buffer),
        )
        return projected <= state.cycle_risk_budget + Decimal("0.01")


@dataclass(frozen=True)
class RecoveryParameters:
    # [经验形状] [base+lower*d, base+upper*d]是给定规则，未找到事件回归来源。
    # 可计算不等于已证明：精确的7/26/1.5/1.8/.30保留待讨论。
    minimum_release_days: float = 7.0
    base_days: float = 26.0
    lower_slope: float = 1.5
    upper_slope: float = 1.8
    risk_coefficient: float = 0.30


class RecoveryModel:
    """硬止损后的 6 小时门槛和多仓日线 L-U 风险区。"""

    def __init__(self, parameters: RecoveryParameters) -> None:
        self.parameters = parameters

    @staticmethod
    def reset_after_hard_stop(state: DirectionState, now_ns: int) -> None:
        state.ma52_recovery_waiting = True
        state.ma52_recovery_started_ns = now_ns
        state.ma52_recovery_released_ns = None
        state.daily_ma52_recovery_confirmed = False
        state.daily_ma52_risk_zone_active = False
        state.hard_stop_count += 1

    @staticmethod
    def on_six_hour_bar(
        state: DirectionState,
        bar: CompletedBar,
        ma52: float,
    ) -> bool:
        if not state.ma52_recovery_waiting:
            return False
        recovered = bar.low >= ma52
        if not recovered:
            return False
        state.ma52_recovery_waiting = False
        state.ma52_recovery_released_ns = bar.ts_end
        return True

    @staticmethod
    def on_daily_bar(state: DirectionState, bar: CompletedBar, ma52: float) -> bool:
        if (
            state.ma52_recovery_started_ns is None
            or state.daily_ma52_recovery_confirmed
            or bar.ts_start < state.ma52_recovery_started_ns
            or bar.low < ma52
        ):
            return False
        state.daily_ma52_recovery_confirmed = True
        state.daily_ma52_risk_zone_active = False
        return True

    def risk_coefficient(self, state: DirectionState, now_ns: int) -> float:
        active = False
        if (
            not state.daily_ma52_recovery_confirmed
            and state.ma52_recovery_started_ns is not None
            and state.ma52_recovery_released_ns is not None
        ):
            release_days = (
                state.ma52_recovery_released_ns - state.ma52_recovery_started_ns
            ) / NANOSECONDS_PER_DAY
            if release_days >= self.parameters.minimum_release_days:
                elapsed_days = (now_ns - state.ma52_recovery_started_ns) / NANOSECONDS_PER_DAY
                lower = self.parameters.base_days + self.parameters.lower_slope * release_days
                upper = self.parameters.base_days + self.parameters.upper_slope * release_days
                active = lower <= elapsed_days <= upper
        state.daily_ma52_risk_zone_active = active
        return self.parameters.risk_coefficient if active else 1.0
