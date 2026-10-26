"""黄金比例幂函数，将历史最大浮盈映射为利润保护比例。"""

from math import isfinite, sqrt


def peak_profit_lock_ratio(peak_return: float) -> float:
    """由持仓历史最大浮盈返回利润保护比例，两者均使用小数。

    浮盈使用相对实际开仓价的有利价格变化，不乘杠杆、不扣手续费。
    1.55% 以下返回 40%，3.5% 及以上返回 95%；区间内使用
    黄金比例的倒数作为幂指数。这是固定经验曲线，不是统计规律；
    端点与指数未提供市场校准证据。默认关闭。返回比例不表示已经启动止盈。
    """
    if not isfinite(peak_return) or peak_return < 0:
        raise ValueError("历史最大浮盈必须为有限非负数")
    progress = max(0.0, min(1.0, (peak_return - 0.0155) / (0.035 - 0.0155)))
    inverse_phi = (sqrt(5.0) - 1.0) / 2.0
    return 0.40 + 0.55 * progress**inverse_phi
