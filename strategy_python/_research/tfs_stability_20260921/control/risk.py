"""把标准化 MA 斜率映射为连续风险，只用于收紧退出保护线。"""

from strategy_python.trend_following_short_5min.risk import SlopeRiskModel


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



__all__ = ["SlopeRiskModel", "profit_risk_multiplier", "tightened_level"]
