"""Benchmark a local trades archive without writing to the production catalog."""

import argparse
import json
import resource
import time
from datetime import date
from pathlib import Path

from pipeline.bar_features import aggregate_feature_archive, calculate_features
from pipeline.models import ArchiveSpec, SyncedArchive


def main():
    parser = argparse.ArgumentParser(description="测量本地 trades 聚合和指标计算，不写生产数据")
    parser.add_argument("archive", type=Path, help="SYMBOL-trades-YYYY-MM-DD.zip")
    parser.add_argument("--intervals", nargs="+", default=["1s", "1m", "5m", "15m"])
    parser.add_argument("--chunk-rows", type=int, default=250_000)
    args = parser.parse_args()
    symbol, day = args.archive.stem.rsplit("-trades-", 1)
    archive = SyncedArchive(
        ArchiveSpec(
            symbol=symbol,
            data_type="trades",
            day=date.fromisoformat(day),
            source_path=str(args.archive),
        ),
        args.archive,
    )
    started, cpu = time.perf_counter(), time.process_time()
    frames = aggregate_feature_archive(
        archive,
        tuple(args.intervals),
        chunk_rows=args.chunk_rows,
    )
    aggregation_seconds = time.perf_counter() - started
    rows = {}
    for interval, frame in frames.items():
        output, _ = calculate_features(frame, interval)
        rows[interval] = len(output)
    print(
        json.dumps(
            {
                "archive": str(args.archive.resolve()),
                "chunk_rows": args.chunk_rows,
                "trades": int(next(iter(frames.values())).trade_count.sum()),
                "bars": rows,
                "aggregation_seconds": aggregation_seconds,
                "indicators_seconds": time.perf_counter() - started - aggregation_seconds,
                "cpu_seconds": time.process_time() - cpu,
                "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                "scope": "local ZIP read, validation, feature aggregation and indicators; excludes catalog writes",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
