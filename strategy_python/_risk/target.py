"""通用目标仓位风险接口的基础实现。"""

from strategy_python._model import PositionTarget


class AllowAllTargetRisk:
    """不修改目标的显式风险层，供无额外风控的示例策略使用。"""

    def apply(self, target: PositionTarget | None) -> PositionTarget | None:
        return target
