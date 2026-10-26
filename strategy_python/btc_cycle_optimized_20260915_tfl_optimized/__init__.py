"""Legacy import compatibility; the strategy implementation lives in one canonical package."""

import sys as _sys
from importlib import import_module as _import_module

_canonical = _import_module("strategy_python.btc_cycle_optimized_20260915_tfl_original")
for _name in ('bars', 'config', 'execution', 'indicators', 'preset', 'profit_protection', 'risk', 'signal', 'sizing', 'state', 'strategy'):
    _sys.modules[f"{__name__}.{_name}"] = _import_module(f"strategy_python.btc_cycle_optimized_20260915_tfl_original.{_name}")
__all__ = list(_canonical.__all__) + ["create_config", "create_strategy"]
for _name in __all__:
    globals()[_name] = getattr(_canonical, _name)
