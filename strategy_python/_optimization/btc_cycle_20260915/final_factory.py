"""Use only the defaults of each independently materialized package."""
import importlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
STRATEGIES=json.loads((ROOT/'final_strategies.json').read_text())
def create_strategy(asset,key,start=None,*,variant='main',**overrides):
    if asset!='BTC' or variant!='main' or overrides:
        raise ValueError('Final verification must use the frozen BTC preset')
    return importlib.import_module('strategy_python.'+STRATEGIES[key]['package']).create_strategy(start)
