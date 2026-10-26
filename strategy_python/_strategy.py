"""Project lifecycle compatibility for NautilusTrader 2.0.0rc7."""
from functools import wraps

from nautilus_trader.trading import Strategy as NativeStrategy


class Strategy(NativeStrategy):
    """Run explicit-stop cleanup before rc7 borrows its Rust strategy mutably."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        callback = cls.__dict__.get("on_stop")
        if callback is None:
            return

        @wraps(callback)
        def on_stop(self):
            if not getattr(self, "_native_stop_in_progress", False):
                return callback(self)
            return None

        cls.on_stop = on_stop

    def stop(self):
        if not self.is_running():
            return super().stop()
        self.on_stop()
        self._native_stop_in_progress = True
        try:
            return super().stop()
        finally:
            self._native_stop_in_progress = False
