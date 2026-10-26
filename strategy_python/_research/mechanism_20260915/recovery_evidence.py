"""Count observed hard-stop episodes; do not infer unrecorded recovery timestamps."""

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent
SOURCES = {
    "ETH_original_train": BASE / "four_strategy_20260914/runs/MRL/train/baseline/fills.csv",
    "ETH_optimized_train": BASE
    / "four_strategy_20260914/runs/MRL/train/moderate_discount/fills.csv",
    "ETH_optimized_full": BASE
    / "four_strategy_20260914/frozen_runs/MRL/full/moderate_discount/fills.csv",
    "BTC_original_full": BASE / "btc_transfer_20260914/runs/full/MRL_original/fills.csv",
    "BTC_optimized_full": BASE / "btc_transfer_20260914/runs/full/MRL_optimized/fills.csv",
}


def main():
    output = []
    for name, path in SOURCES.items():
        frame = pd.read_csv(path)
        tags = frame.tags.fillna("")
        stops = frame[tags.str.contains(":hard-stop:", regex=False)].copy()
        emergencies = frame[tags.str.contains(":emergency:", regex=False)]
        times = pd.to_datetime(stops.ts_last, utc=True)
        output.append(
            {
                "source": name,
                "fills_file": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "stop_positions": stops.position_id.nunique(),
                "stop_months": times.dt.strftime("%Y-%m").nunique(),
                "stop_timestamps": sorted(times.astype(str).tolist()),
                "emergency_fills": len(emergencies),
            }
        )
    (ROOT / "recovery_event_counts.json").write_text(json.dumps(output, indent=2) + "\n")
    lines = [
        "# Q3恢复时间带：可用事件数量",
        "",
        "| 数据 | 硬止损持仓数 | 涉及月份数 | 应急成交数 |",
        "|---|---:|---:|---:|",
    ]
    for row in output:
        lines.append(
            f"| {row['source']} | {row['stop_positions']} | {row['stop_months']} | {row['emergency_fills']} |"
        )
    lines += [
        "",
        "计数来自实际成交的hard-stop标签，记录和哈希见[原始证据](recovery_event_counts.json)。原/优化版、ETH/BTC事件会重合，不能把这些数量相加当独立样本。旧ETH全历史截止日与本轮相差一天，仅用于事件数量核查，不用于收益对照。",
        "",
        "成交表没有完整记录MA恢复释放时间、所有等待状态及26+1.5d到26+1.8d窗口的反事实结果。因此当前证据不能估计或确认7、26、1.5、1.8、0.30这些精确值。候选保留恢复机制，不把它们重新拟合成另一组缺乏依据的数字。",
    ]
    (ROOT / "RECOVERY_EVIDENCE.md").write_text("\n".join(lines) + "\n")
    print(
        json.dumps(
            [{k: row[k] for k in ("source", "stop_positions", "stop_months")} for row in output]
        )
    )


if __name__ == "__main__":
    main()
