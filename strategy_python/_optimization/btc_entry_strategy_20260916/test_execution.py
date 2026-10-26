"""Guard execution ordering that can materially change the historical signal path."""

import unittest

import numpy as np
import pandas as pd

from strategy_python.btc_failed_breakout_20260916.strategy import Config, find_exit, simulate


class ExecutionTest(unittest.TestCase):
    def test_gap_stop_and_same_bar_priority(self):
        step = 60_000_000_000
        t = np.arange(4, dtype="int64") * step
        o = np.array([100.0, 90.0, 100.0, 100.0])
        hi = np.array([101.0, 110.0, 101.0, 101.0])
        lo = np.array([99.0, 89.0, 99.0, 99.0])
        self.assertEqual(find_exit(o, hi, lo, t, 0, 3 * step, 95.0, 105.0, 4), (1, 90.0, "stop"))
        o[1] = 100.0
        lo[1] = 94.0
        self.assertEqual(find_exit(o, hi, lo, t, 0, 3 * step, 95.0, 105.0, 4), (1, 95.0, "stop"))
        self.assertEqual(find_exit(o, hi, lo, t, 0, step, 95.0, 105.0, 4), (1, 100.0, "time"))
        t[1:] += step
        self.assertEqual(
            find_exit(o, hi, lo, t, 0, 4 * step, 95.0, 105.0, 4), (1, 100.0, "data_gap")
        )

    def test_reentry_only_after_exit_known(self):
        start = pd.Timestamp("2021-01-01", tz="UTC")
        idx = pd.date_range(start + pd.Timedelta(minutes=1), periods=130, freq="min")
        m = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}, index=idx)
        s = pd.DataFrame(
            {"low": [95.0, 95.0], "atr": [10.0, 10.0]}, index=[start, start + pd.Timedelta(hours=1)]
        )
        m.loc[start + pd.Timedelta(minutes=62), "low"] = 93.0
        _, t, _ = simulate(
            m, s, Config("stop", 48, "structure"), start, start + pd.Timedelta(minutes=130)
        )
        self.assertEqual(len(t), 1)  # Later signal's open precedes intraminute stop discovery.
        _, t, _ = simulate(m, s, Config("time1", 1), start, start + pd.Timedelta(minutes=130))
        self.assertEqual(len(t), 2)  # Time exit is known at the open, so close then reopen.
        self.assertEqual(t.iloc[0].exit_time, t.iloc[1].entry_time)


if __name__ == "__main__":
    unittest.main()
