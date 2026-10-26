"""Next-cycle isolated packages: bounded MRL units and post-failure rearm."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[2]
registry=json.loads((ROOT/'runtime_strategies.json').read_text())
factory_registry=json.loads((ROOT/'strategies.json').read_text())
for slot,info in registry.items():
    old=info['package'];new=old.replace('.packages.','.next_packages.')
    dest=PROJECT/'strategy_python'/new.replace('.','/');dest.mkdir(parents=True,exist_ok=True)
    for src in (PROJECT/'strategy_python'/old.replace('.','/')).glob('*.py'):
        s=src.read_text().replace('strategy_python.'+old,'strategy_python.'+new)
        if slot.startswith('MRL'):
            if src.name=='config.py':s+='\n    cycle_rearm_after_exit: bool = False\n'
            if src.name=='strategy.py':
                s=s.replace('if config.max_units != 4:', 'if config.max_units not in (1, 2, 3, 4):').replace('当前 v4 档案固定使用 4 份；变更前需重新标定','Research allocation supports one to four existing ladder levels')
                s=s.replace('        self._cycle_exiting = set()', '        self._cycle_exiting = set()\n        self._cycle_rearm = set()',1)
                s=s.replace('                self._cycle_exiting.add(state.direction)', '                self._cycle_exiting.add(state.direction)\n                if self.config.cycle_rearm_after_exit:\n                    self._cycle_rearm.add(state.direction)',1)
                s=s.replace('            if state.direction in self._cycle_exiting:\n                continue', '''            if state.direction in self._cycle_rearm:
                if not state.invested and self.features.bollinger is not None and bar.close >= self.features.bollinger[1]:
                    self._cycle_rearm.discard(state.direction)
                else:
                    self._cancel_entry(state)
                    if not state.invested:
                        continue
            if state.direction in self._cycle_exiting:
                continue''',1)
        if slot.startswith('TFL') and src.name=='strategy.py':
            s=s.replace('if not 0 < config.equity_fraction_per_side <= 1:', 'if not isfinite(config.equity_fraction_per_side) or config.equity_fraction_per_side <= 0:').replace('equity_fraction_per_side 必须位于 (0, 1]', 'equity_fraction_per_side 必须为有限正数')
        (dest/src.name).write_text(s)
    info['package']=new;factory_registry[slot]['package']=new
(ROOT/'next_runtime_strategies.json').write_text(json.dumps(registry,indent=2)+'\n')
(ROOT/'next_strategies.json').write_text(json.dumps(factory_registry,indent=2)+'\n')
s=(ROOT/'factory.py').read_text().replace('ROOT / "strategies.json"','ROOT / "next_strategies.json"')
(ROOT/'next_factory.py').write_text(s)
s=(ROOT/'run.py').read_text().replace('ROOT / "runtime_strategies.json"','ROOT / "next_runtime_strategies.json"').replace('ROOT / "experiments.json"','ROOT / "next_experiments.json"').replace('btc_cycle_20260915.factory','btc_cycle_20260915.next_factory').replace('ROOT / "factory.py"','ROOT / "next_factory.py"').replace('ROOT / "strategies.json"','ROOT / "next_strategies.json"')
(ROOT/'next_run.py').write_text(s)
experiments={}
for slot in registry:
    ex={'next_base':{'overrides':{}}}
    if slot.startswith('MRL'):
        for units in [1,2]:
            ex[f'space_units{units}_same_risk']={'overrides':{'max_units':units}}
            ex[f'space_units{units}_same_unit']={'overrides':{'max_units':units,'cycle_risk_fraction':.3*units/4}}
            ex[f'space_flat_units{units}']={'base_variant':'flat_risk','overrides':{'max_units':units}}
        for hours in [24,48]:
            ex[f'next_time{hours}_rearm']={'overrides':{'cycle_time_exit_hours':hours,'cycle_rearm_after_exit':True}}
    elif slot.startswith(('TFL','TFS')):
        ex['next_slope_level']={'overrides':{'entry_on_slope_change':False}}
        if slot.startswith('TFS'):ex['next_slope_level']['overrides']['require_hourly_slope_filter']=True
        for mult in [.5,1.5]:
            ex[f'space_budget{mult}']={'overrides':{'mechanism_price_risk':.005*mult,'equity_fraction_per_side':1.*mult}}
        for cap in [.5,2.]:ex[f'space_cap{cap}']={'overrides':{'equity_fraction_per_side':cap}}
    else:
        for risk in [.005,.01,.02]:ex[f'space_risk{risk}']={'overrides':{'risk_fraction':risk}}
        for cap in [1.,2.]:ex[f'space_cap{cap}']={'overrides':{'max_notional_fraction':cap}}
    for lev in [2,10]:ex[f'space_margin{lev}']={'account_leverage':lev,'overrides':{}}
    experiments[slot]=ex
(ROOT/'next_experiments.json').write_text(json.dumps(experiments,indent=2)+'\n')
print('NEXT_EIGHT_PACKAGES_CREATED')
