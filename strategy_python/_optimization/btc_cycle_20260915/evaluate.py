"""Declared sequential stages, two independent native engines maximum."""
import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[2]
UNIQUE=['MRL_original','MRL_optimized','TFL_original','TFS_original','PBS_original']

def execute(case):
    slot,period,variant=case
    tag=f'{slot}_{period}_{variant}'
    print('START',tag,flush=True)
    with (ROOT/(tag+'.log')).open('w') as log:
        args=[sys.executable,'-m','strategy_python._optimization.btc_cycle_20260915.run',slot,period,'--variant',variant]
        if period=='stress':args+=['--batch-days','7']
        subprocess.run(args,cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT,check=True)
    print('DONE',tag,flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['smoke','round1']);a=parser.parse_args()
    exp=json.loads((ROOT/'experiments.json').read_text())
    if a.stage=='smoke':
        cases=[(slot,'stress','main') for slot in UNIQUE]
        cases += [('MRL_original','stress','time24'),('MRL_original','stress','band8'),('TFL_original','stress','time24'),('TFS_original','stress','clock15'),('PBS_original','stress','falling_ma')]
    else:
        cases=[(slot,'develop',variant) for slot in UNIQUE for variant in exp[slot]]
    (ROOT/(a.stage+'_cases.json')).write_text(json.dumps(cases,indent=2)+'\n')
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(execute,cases))
    print('ALL_COMPLETE',a.stage,len(cases),flush=True)
