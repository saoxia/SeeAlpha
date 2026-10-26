"""Four mechanism follow-ups, retaining the first round unchanged."""

import hashlib
import json
from pathlib import Path

import pandas as pd

from . import research

original_features = research.features


def features(frame):
    h, original, valid = original_features(frame)
    ma = h.close.rolling(168).mean()
    low = h.low.shift().rolling(24).min()
    high = h.high.shift().rolling(24).max()
    signals = pd.DataFrame(
        {
            "trend_reversion_long": original.reversion_long & (h.close > ma) & (ma > ma.shift(24)),
            "trend_reversion_short": original.reversion_short
            & (h.close < ma)
            & (ma < ma.shift(24)),
            "failed_breakout_long": (h.low < low) & (h.close > low),
            "failed_breakout_short": (h.high > high) & (h.close < high),
        },
        index=h.index,
    )
    return h, signals & valid.to_numpy()[:, None], valid


if __name__ == "__main__":
    research.ROOT = Path(__file__).resolve().parent / "second"
    research.features = features
    research.NAMES = {
        "trend_reversion_long": "周趋势向上时超跌做多",
        "trend_reversion_short": "周趋势向下时超涨做空",
        "failed_breakout_long": "向下假突破收复做多",
        "failed_breakout_short": "向上假突破跌回做空",
    }
    research.main()
    path = research.ROOT / "provenance.json"
    provenance = json.loads(path.read_text())
    provenance[str(Path(__file__))] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    path.write_text(json.dumps(provenance, indent=2) + "\n")
