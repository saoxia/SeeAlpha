"""Seven declared BTC historical replays, at most two engines at once."""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
CASES = [(key, 'main') for key in ['BTC_MRL_original','BTC_MRL_optimized','BTC_TFL','BTC_TFS','BTC_PBS']]
CASES += [(key, 'flat_risk') for key in ['BTC_MRL_original','BTC_MRL_optimized']]

def execute(case):
    key,variant=case
    print('START',key,variant,flush=True)
    with (ROOT/f'{key}_{variant}.log').open('w') as log:
        subprocess.run([sys.executable,'-m','strategy_python._optimization.mechanism_full_20260915.run',key,'full','--variant',variant],cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT,check=True)
    print('DONE',key,variant,flush=True)

if __name__=='__main__':
    (ROOT/'cases.json').write_text(json.dumps(CASES,indent=2)+'\n')
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(execute,CASES))
    print('ALL_COMPLETE',len(CASES),flush=True)
