"""3x sizing comparison with 3x account margin leverage; defaults stay unchanged."""
import argparse
import importlib.util
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from strategy_python._backtesting import StrategyBacktest
from strategy_python.pullback_short_1h.backtest import BACKTEST_SPEC, create_strategy

ROOT=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser();parser.add_argument('period',choices=['full','holdout']);args=parser.parse_args()
    start=datetime(2021 if args.period=='full' else 2025,1,1,tzinfo=UTC)
    runner=StrategyBacktest('pullback_short_1h_3x',replace(BACKTEST_SPEC,leverage=Decimal(3)),create_strategy)
    report=runner.run(start,datetime(2026,9,1,tzinfo=UTC),risk_fraction=.015,max_notional_fraction=3.)
    if report is None:raise RuntimeError('No backtest data')
    module_spec=importlib.util.spec_from_file_location('pullback_native_audit',ROOT.parent/'pullback_short_20260913'/'audit_native.py')
    module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
    result=module.audit(report)
    (ROOT/f'{args.period}_audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print('COMPLETED',args.period,report,flush=True)


if __name__=='__main__':main()
