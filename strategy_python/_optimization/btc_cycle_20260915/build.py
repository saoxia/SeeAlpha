"""Build eight independent candidates; never modify prior code."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[2]
BASE=ROOT.parent/'mechanism_full_20260915'
SLOTS=json.loads((ROOT/'slots.json').read_text())
OLD=json.loads((BASE/'runtime_strategies.json').read_text())
registry={};factory_registry={};experiments={}
for slot,basekey in SLOTS.items():
    info=OLD[basekey];old_package=info['package']
    package='_optimization.btc_cycle_20260915.packages.'+slot.lower()
    family=slot.split('_')[0]
    target=PROJECT/'strategy_python'/package.replace('.','/')
    target.mkdir(parents=True,exist_ok=True)
    for src in (PROJECT/'strategy_python'/old_package.replace('.','/')).glob('*.py'):
        s=src.read_text().replace('strategy_python.'+old_package,'strategy_python.'+package)
        if src.name=='config.py':
            if family=='MRL':s+='\n    cycle_time_exit_hours: float = 0.0\n    cycle_band_stop_multiple: float = 0.0\n'
            elif family in ('TFL','TFS'):s+='\n    cycle_time_exit_hours: float = 0.0\n    cycle_signal_minutes: int = 5\n    cycle_min_reward_cost_ratio: float = 0.0\n'
            else:s+='\n    cycle_require_falling_ma: bool = False\n'
        if src.name=='strategy.py':
            if family=='MRL':
                s=s.replace('        super().__init__(config)','        super().__init__(config)\n        self._cycle_exiting = set()',1)
                s=s.replace('        completed = self.features.aggregate(bar)','        self._check_cycle_exits(bar)\n        completed = self.features.aggregate(bar)',1)
                s=s.replace('            if state.invested:\n                self._refresh_add_order', '            if state.direction in self._cycle_exiting:\n                continue\n            if state.invested:\n                self._refresh_add_order',1)
                marker='    def _on_five_minute_bar('
                pos=s.index(marker)
                s=s[:pos]+'''    def _check_cycle_exits(self, bar):
        for state in self.legs:
            if not state.invested:
                self._cycle_exiting.discard(state.direction)
                continue
            if state.position_start_ns is None or bar.ts_event <= state.position_start_ns:
                continue
            reason = None
            if (self.config.cycle_time_exit_hours > 0 and
                bar.ts_event - state.position_start_ns >= self.config.cycle_time_exit_hours * NANOSECONDS_PER_HOUR):
                reason = "cycle_time_exit"
            snapshot = state.cycle_snapshot
            if (self.config.cycle_band_stop_multiple > 0 and snapshot is not None and
                float(bar.close) <= float(state.average_price) * (1 - snapshot.take_profit_return * self.config.cycle_band_stop_multiple)):
                reason = "cycle_structure_exit"
            if reason is not None or state.direction in self._cycle_exiting:
                self._cycle_exiting.add(state.direction)
                self._cancel_state_orders(state)
                self._submit_emergency_exit(state, reason or "cycle_exit_retry", role="cycle_exit")

'''+s[pos:]
            elif family in ('TFL','TFS'):
                s=s.replace('self.five_minute = FiveMinuteAggregator(source_seconds=source_seconds)','self.five_minute = CompletedBarAggregator(config.cycle_signal_minutes, source_seconds=source_seconds)')
                s=s.replace('        if self.config.peak_profit_exit:\n            self._update_peak_exits(bar)', '''        if self.config.cycle_time_exit_hours > 0:
            for state in self.legs:
                if state.invested and bar.ts_event - state.last_entry_fill_ns >= self.config.cycle_time_exit_hours * 3600_000_000_000:
                    self.cancel_entry(state)
                    self.submit_exit(state, half=False)
        if self.config.peak_profit_exit:
            self._update_peak_exits(bar)''',1)
                s=s.replace('        if snapshot is None:\n            return\n        self.mechanism_snapshot = snapshot','        if snapshot is None:\n            return\n        if snapshot.activation_return < self.config.cycle_min_reward_cost_ratio * 0.001:\n            return\n        self.mechanism_snapshot = snapshot',1)
            else:
                s=s.replace('        if not short_setup(features):','        if self.config.cycle_require_falling_ma and features.ma >= features.previous_ma:\n            return\n        if not short_setup(features):',1)
        if src.name=='execution.py' and family=='MRL':
            s=s.replace('meta.role in ("stop_emergency", "orphan_exit")','meta.role in ("stop_emergency", "orphan_exit", "cycle_exit")')
            s=s.replace('elif meta.role == "take_profit":\n                state.cooldown_until_ns', 'elif meta.role in ("take_profit", "cycle_exit"):\n                state.cooldown_until_ns')
        (target/src.name).write_text(s)
    registry[slot]=dict(info,package=package,slot=slot)
    factory_registry[slot]=dict(info,package=package,mechanism_key=info['slot'])
    exp={'main':{'overrides':{}}}
    if family=='MRL':
        exp.update({f'time{h}':{'overrides':{'cycle_time_exit_hours':h}} for h in [24,48,72]})
        exp.update({f'band{k}':{'overrides':{'cycle_band_stop_multiple':k}} for k in [4,8]})
        exp['flat']={'base_variant':'flat_risk','overrides':{}}
    elif family in ('TFL','TFS'):
        exp.update({f'time{h}':{'overrides':{'cycle_time_exit_hours':h}} for h in [24,48]})
        exp.update({f'clock{h}':{'overrides':{'cycle_signal_minutes':h}} for h in [15,30]})
        exp.update({f'cost{k}':{'overrides':{'cycle_min_reward_cost_ratio':k}} for k in [3,5]})
        exp.update({f'lock{k}':{'overrides':{'peak_profit_lock':k/100}} for k in [50,80]})
    else:
        exp.update({f'time{h}':{'overrides':{'max_holding_hours':h}} for h in [12,48]})
        exp.update({f'reward{k}':{'overrides':{'reward_risk':k}} for k in [2,3]})
        exp['falling_ma']={'overrides':{'cycle_require_falling_ma':True}}
    experiments[slot]=exp
(ROOT/'runtime_strategies.json').write_text(json.dumps(registry,indent=2)+'\n')
(ROOT/'strategies.json').write_text(json.dumps(factory_registry,indent=2)+'\n')
(ROOT/'experiments.json').write_text(json.dumps(experiments,indent=2)+'\n')
f=(BASE/'factory.py').read_text().replace('    info = STRATEGIES[key]','    info = STRATEGIES[key]\n    key = info["mechanism_key"]')
(ROOT/'factory.py').write_text(f)
s=(BASE/'run.py').read_text().replace('mechanism_full_20260915.factory','btc_cycle_20260915.factory')
s=s.replace('PERIODS = {','EXPERIMENTS = json.loads((ROOT / "experiments.json").read_text())\nPERIODS = {\n    "develop": ("2021-01-01", "2024-01-01"),')
s=s.replace('def spec_for(key):','def spec_for(key, leverage=5):').replace('leverage=Decimal(5),','leverage=Decimal(str(leverage)),')
s=s.replace('return create(info["asset"], info["slot"], start, variant=variant)','experiment = EXPERIMENTS[key][variant]\n        return create(info["asset"], info["slot"], start, variant=experiment.get("base_variant", "main"), **experiment["overrides"])')
s=s.replace('    spec = spec_for(key)\n    strategy = create_strategy', '    experiment = EXPERIMENTS[key][variant]\n    spec = spec_for(key, experiment.get("account_leverage", 5))\n    strategy = create_strategy')
s=s.replace('"training_overlap": period == "full",','"training_overlap": period in ("full", "develop"),\n        "experiment": experiment,')
s=s.replace('"account_leverage": 5,','"account_leverage": float(spec.leverage),').replace('default_leverage=Decimal(5),','default_leverage=spec.leverage,')
s=s.replace('default_leverage=Decimal(str(leverage)),','default_leverage=spec.leverage,')
(ROOT/'run.py').write_text(s)
print('BUILT_INDEPENDENT_SLOTS',len(registry))
