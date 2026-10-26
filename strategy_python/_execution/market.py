"""把目标仓位转换为 NautilusTrader 市价单。"""

from nautilus_trader.model import InstrumentId, OrderSide, TimeInForce

from nautilus_support import Instrument
from strategy_python._model import PositionSide, PositionTarget
from strategy_python._strategy import Strategy


class MarketTargetExecutor:
    """简单策略共用的目标仓位执行适配器。"""

    def __init__(self, strategy: Strategy, instrument_id: InstrumentId) -> None:
        self._strategy = strategy
        self._instrument_id = instrument_id
        self._instrument: Instrument | None = None

    def bind(self, instrument: Instrument) -> None:
        """绑定由策略缓存取得的交易合约。"""
        self._instrument = instrument

    def apply(self, target: PositionTarget | None) -> None:
        """在没有活动订单时把账户仓位移向目标。"""
        if target is None or self._instrument is None:
            return
        if self._strategy.cache.orders_open_count(
            instrument_id=self._instrument_id,
            strategy_id=self._strategy.strategy_id,
        ):
            return
        if target.side == PositionSide.FLAT:
            self._strategy.close_all_positions(self._instrument_id)
            return

        side = OrderSide.BUY if target.side == PositionSide.LONG else OrderSide.SELL
        order = self._strategy.order_factory.market(
            instrument_id=self._instrument_id,
            order_side=side,
            quantity=self._instrument.make_qty(target.quantity),
            time_in_force=TimeInForce.IOC,
            tags=[f"target:{target.reason}"],
        )
        self._strategy.submit_order(order)

    def reset(self) -> None:
        self._instrument = None
