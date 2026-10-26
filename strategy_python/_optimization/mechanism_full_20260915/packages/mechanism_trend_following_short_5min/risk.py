"""把标准化 MA 斜率映射为连续风险，只用于收紧退出保护线。"""

from math import isfinite


def profit_risk_multiplier(profit_return: float, scale: float) -> float:
    """正浮盈越大，额外收紧越小；亏损不放松，收益不乘杠杆。"""
    return 1 / (1 + max(0.0, profit_return) / scale)


def tightened_level(
    base: float,
    close: float,
    std: float,
    direction: int,
    risk: float,
    *,
    rule: str,
    max_tightening_std: float,
    price_buffer_std: float,
    multiplier: float,
) -> float:
    """只生成更紧的候选线；历史棘轮和价格触发仍由现有退出逻辑处理。"""
    if rule == "fixed":
        shift = risk * max_tightening_std * std * multiplier
    elif rule == "distance":
        # 不使用未来价格：最多靠近当前已完成收盘价，保留波动缓冲。
        available = max(0.0, direction * (close - base) - price_buffer_std * std)
        shift = risk * multiplier * available
    else:
        raise ValueError("未知风险保护线算法")
    return base + direction * shift


class SlopeRiskModel:
    def __init__(self, ema_period: int = 3, slope_scale: float = 0.1) -> None:
        if ema_period < 1 or not isfinite(slope_scale) or slope_scale <= 0:
            raise ValueError("风险平滑周期必须为正，斜率尺度必须为有限正数")
        self.alpha = 2 / (ema_period + 1)
        self.slope_scale = slope_scale
        self.smoothed_slope: float | None = None

    def update(self, slope: float | None, std: float) -> None:
        if slope is None:
            return
        # 零波动视为中性；当前 σ=0 时保护线位移也为零。
        normalized = slope / std if std > 0 else 0.0
        if self.smoothed_slope is None:
            self.smoothed_slope = normalized
        else:
            self.smoothed_slope += self.alpha * (normalized - self.smoothed_slope)

    def coefficient(self, direction: int) -> float:
        if direction not in (-1, 1):
            raise ValueError("方向只能是 1（多）或 -1（空）")
        if self.smoothed_slope is None:
            return 0.0
        favorable_slope = direction * self.smoothed_slope
        # [数学构造] 在顺向斜率=-scale/0/+scale时映射到1/.5/0，区间外截断。
        # .5和2由线性端点约束得到；scale和这个映射选择仍未有统计校准证据。
        return max(0.0, min(1.0, 0.5 - favorable_slope / (2 * self.slope_scale)))

    def reset(self) -> None:
        self.smoothed_slope = None
