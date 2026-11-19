"""Demo: two independent signals -> weighted target -> Engine netting.

Run mode=trend and mode=reversion as two separately issued instances to
demonstrate cross-strategy netting, or mode=combined for one package target.
The strategy only publishes targets; it never submits orders directly.
"""

from nautilus_trader.trading import Strategy

from .logic import MultiSignalModel, prepare_parameters


def prepare(parameters):
    return prepare_parameters(parameters)


class DemoMultiSignalPortfolio(Strategy):
    def __init__(self, config):
        super().__init__(config)
        self.model = None
        self._last_ts = None
        self._last_target = None

    def on_start(self):
        self.model = MultiSignalModel(self.parameters)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        if bar.bar_type != self.bar_type:
            return
        if self._last_ts is not None and bar.ts_event <= self._last_ts:
            return  # Duplicate/out-of-order bars must not double-advance signals.
        self._last_ts = bar.ts_event
        result = self.model.on_close(bar.close.as_decimal())
        if result is None:
            return  # Wait for the selected indicators to warm up.
        target = result["target"]
        if target != self._last_target:
            self.publish(symbol=self.parameters["symbol"], quantity=target)
            self._last_target = target
