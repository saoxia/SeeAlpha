"""Run frozen scale defaults over full/recent periods, including equal-risk PBS."""

from concurrent.futures import ThreadPoolExecutor

from strategy_python._optimization.btc_scale_20260915.evaluate import KEYS, execute

if __name__ == "__main__":
    cases = [(key, period, False, "stream") for key in KEYS for period in ("full", "recent")]
    cases += [("PBS_original", period, True, "stream") for period in ("full", "recent")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(execute, cases))
    print("ALL_18_COMPLETED", flush=True)
