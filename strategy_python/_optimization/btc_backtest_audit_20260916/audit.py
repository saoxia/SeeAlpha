"""Read-only BTC package/current-source and historical full-run coverage audit."""

import ast
import hashlib
import json
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
STRATEGIES = PROJECT / "strategy_python"
OPT = STRATEGIES / "_optimization"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main():
    runner_hashes = {digest(p) for p in OPT.glob("*/*.py")}
    packages = sorted(
        {
            p.parent
            for p in STRATEGIES.rglob("strategy.py")
            if any(x.startswith(("btc", "mechanism")) for x in p.parts)
            and not any("archive" in x for x in p.parts)
        }
    )
    # Include no archived failed/obsolete runs and require actual completed artifacts.
    records, invalid = [], []
    for path in OPT.rglob("parameters.json"):
        if any("archive" in x for x in path.parts):
            continue
        p = json.loads(path.read_text())
        if "BTC" not in str(p.get("config", {}).get("instrument_id", "")):
            continue
        if not (path.parent / "metrics.json").is_file():
            continue
        hashes = p.get("source_sha256", {})
        mismatches = [s for s, h in hashes.items() if digest(PROJECT / s) != h]
        belongs = [
            str(pkg.relative_to(PROJECT))
            for pkg in packages
            if str((pkg / "strategy.py").relative_to(PROJECT)) in hashes
        ]
        for pkg in belongs:
            files = {str(f.relative_to(PROJECT)) for f in (PROJECT / pkg).glob("*.py")}
            missing = sorted(files - set(hashes))
            row = {
                "package": pkg,
                "run": str(path.parent.relative_to(PROJECT)),
                "start": p.get("start"),
                "end": p.get("end"),
                "variant": p.get("variant", "main"),
                "key": p.get("key"),
                "hash_mismatches": mismatches,
                "unrecorded_package_py": missing,
                "runner_current": p.get("runner_sha256") in runner_hashes,
                "source_current": bool(hashes)
                and not mismatches
                and not missing
                and p.get("runner_sha256") in runner_hashes,
                "artifacts_complete": all(
                    (path.parent / f).is_file() for f in ["fills.csv", "daily_equity.csv"]
                ),
            }
            row["starts_2021"] = str(row["start"]).startswith("2021-01-01")
            row["full"] = row["starts_2021"] and str(row["end"]).startswith("2026-08-31")
            records.append(row)
            if mismatches or missing or not row["runner_current"]:
                invalid.append(row)
    result = []
    for pkg in packages:
        name = str(pkg.relative_to(PROJECT))
        found = [r for r in records if r["package"] == name]
        valid = [r for r in found if r["source_current"] and r["artifacts_complete"]]
        full = [r for r in valid if r["full"]]
        partial = [r for r in valid if r["starts_2021"]]
        status = (
            "native_full_current"
            if full
            else ("native_2021_partial_only" if partial else "no_current_2021_native_record")
        )
        result.append(
            {
                "package": name,
                "top_level": pkg.parent == STRATEGIES,
                "status": status,
                "current_native_full_runs": len(full),
                "current_2021_start_runs": len(partial),
                "current_native_runs": len(valid),
                "stale_or_incomplete_source_runs": len(found) - len(valid),
                "evidence": full[0]["run"] if full else (partial[0]["run"] if partial else ""),
            }
        )
    # Original mechanism packages were replayed through import-renamed copies.
    ledger_path = OPT / "mechanism_full_20260915/copy_verification.json"
    ledger = json.loads(ledger_path.read_text())
    for row in result:
        if not row["top_level"] or not Path(row["package"]).name.startswith("mechanism_"):
            continue
        source = PROJECT / row["package"]
        copied = OPT / "mechanism_full_20260915/packages" / source.name
        evidence = next(r for r in result if r["package"] == str(copied.relative_to(PROJECT)))
        identical = True
        for path in source.glob("*.py"):
            key = str(path.relative_to(PROJECT))
            entry = ledger.get(key)
            cp = copied / path.name
            if (
                not entry
                or digest(path) != entry["source_sha256"]
                or digest(cp) != entry["copy_sha256"]
            ):
                identical = False
                break
            a = path.read_text().replace(
                "strategy_python." + source.name,
                "strategy_python._optimization.mechanism_full_20260915.packages." + source.name,
            )
            source_ast = ast.parse(a)
            guard = ast.parse(
                'if config.trade_start_ns < 1704067200000000000:\n    raise ValueError("Mechanism calibration ends 2024-01-01; earlier trading is invalid")'
            ).body[0]

            class RemoveDeclaredGuard(ast.NodeTransformer):
                def __init__(self, expected):
                    self.expected = expected

                def visit_If(self, node):
                    if ast.dump(node) == self.expected:
                        return None
                    return self.generic_visit(node)

            source_ast = RemoveDeclaredGuard(ast.dump(guard)).visit(source_ast)
            if ast.dump(source_ast) != ast.dump(ast.parse(cp.read_text())):
                identical = False
                break
        if identical and evidence["status"] == "native_full_current":
            row["status"] = "native_full_via_guard_removed_copy"
            row["evidence"] = evidence["evidence"]
    # The new independent signal strategy uses a custom simulator, not native fills.
    study = OPT / "btc_entry_strategy_20260916"
    source = STRATEGIES / "btc_failed_breakout_20260916/strategy.py"
    hashes = json.loads((study / "followup_provenance.json").read_text())
    stored = hashes.get(str(source)) or hashes.get(str(source.relative_to(PROJECT)))
    verification = json.loads((study / "verification.json").read_text())
    for row in result:
        if row["package"].endswith("/btc_failed_breakout_20260916"):
            passed = digest(source) == stored and verification.get(
                "preset_daily_equity_matches", False
            )
            row["status"] = (
                "custom_full_only_no_native" if passed else "custom_full_provenance_unconfirmed"
            )
            row["evidence"] = str((study / "followup_runs/cap/0.2/full").relative_to(PROJECT))
    # Config variants are separate from whether the package code has any full run.
    variants = []
    cycle = OPT / "btc_cycle_20260915"
    for prefix in ["", "next_", "third_", "fourth_"]:
        experiments = json.loads((cycle / (prefix + "experiments.json")).read_text())
        registry = json.loads(
            (cycle / ("strategies.json" if not prefix else "next_strategies.json")).read_text()
        )
        for slot, choices in experiments.items():
            package = "strategy_python/" + registry[slot]["package"].replace(".", "/")
            for variant in choices:
                matches = [
                    r
                    for r in records
                    if r["package"] == package
                    and r["variant"] == variant
                    and r["source_current"]
                    and r["artifacts_complete"]
                ]
                variants.append(
                    {
                        "round": prefix or "first_",
                        "slot": slot,
                        "variant": variant,
                        "package": package,
                        "from_2021": any(r["starts_2021"] for r in matches),
                        "full_2021_to_end": any(r["full"] for r in matches),
                        "run_count": len(matches),
                    }
                )
    pd.DataFrame(result).to_csv(ROOT / "packages.csv", index=False)
    pd.DataFrame(variants).to_csv(ROOT / "variants.csv", index=False)
    (ROOT / "run_evidence.json").write_text(json.dumps(records, indent=2) + "\n")
    (ROOT / "source_exceptions.json").write_text(json.dumps(invalid, indent=2) + "\n")
    print("PACKAGES", len(result), Counter(r["status"] for r in result))
    print("TOP_LEVEL", Counter(r["status"] for r in result if r["top_level"]))
    for row in result:
        if row["status"] != "native_full_current":
            print(row)
    print(
        "VARIANTS",
        len(variants),
        "FULL",
        sum(r["full_2021_to_end"] for r in variants),
        "2021_START",
        sum(r["from_2021"] for r in variants),
    )
    print("SOURCE_EXCEPTIONS", len(invalid))


if __name__ == "__main__":
    main()
