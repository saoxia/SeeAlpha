"""Run the frozen weekend model with actual Nautilus orders and audit its equity."""
import argparse
import importlib.util
import json
from datetime import UTC,datetime
from pathlib import Path

from strategy_python.mean_reversion_short_v2_5min.backtest import run_backtest
ROOT=Path(__file__).resolve().parent


def main():
 parser=argparse.ArgumentParser();parser.add_argument('period',choices=['full','later']);parser.add_argument('--leverage',type=int,choices=[3,5],default=3);args=parser.parse_args()
 report=run_backtest(datetime(2021 if args.period=='full' else 2025,1,1,tzinfo=UTC),datetime(2026,9,1,tzinfo=UTC),max_leverage=args.leverage)
 if report is None:raise RuntimeError('No data')
 spec=importlib.util.spec_from_file_location('audit',ROOT.parent/'pullback_short_20260913'/'audit_native.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 result=module.audit(report)
 (ROOT/f'native_{args.period}_{args.leverage}x_audit.json').write_text(json.dumps(result,indent=2)+'\n')
 print('COMPLETED',args.period,args.leverage,report,flush=True)

if __name__=='__main__':main()
