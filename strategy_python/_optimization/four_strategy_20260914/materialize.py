"""Create independent named strategy packages from frozen validation selections."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from strategy_python._optimization.four_strategy_20260914.research import BASE, CANDIDATES, NAMES

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
PACKAGES = {
    key: name.replace("_5min", "_optimized_5min").replace(
        "pullback_short_1h", "pullback_short_optimized_1h"
    )
    for key, name in NAMES.items()
}
CLASSES = {
    "MRL": ("MeanReversionLong5Min", "MeanReversionLongOptimized5Min"),
    "TFL": ("TrendFollowingLong5Min", "TrendFollowingLongOptimized5Min"),
    "TFS": ("TrendFollowingShort5Min", "TrendFollowingShortOptimized5Min"),
    "PBS": ("PullbackShort1h", "PullbackShortOptimized1h"),
}


def main():
    selections = {key: json.loads((ROOT / f"{key}_selection.json").read_text()) for key in NAMES}
    for key, old in NAMES.items():
        new = PACKAGES[key]
        source = PROJECT / "strategy_python" / old
        target = PROJECT / "strategy_python" / new
        if target.exists():
            raise FileExistsError(target)
        target.mkdir()
        old_class, new_class = CLASSES[key]
        overrides = {**BASE[key], **CANDIDATES[key][selections[key]["selected"]]}
        for path in source.glob("*.py"):
            text = path.read_text().replace(old, new).replace(old_class, new_class)
            if path.name == "config.py":
                for name, value in overrides.items():
                    text, count = re.subn(
                        rf"^(    {name}: [^=\n]+)= [^\n]+$",
                        rf"\g<1>= {value!r}",
                        text,
                        flags=re.MULTILINE,
                    )
                    if count != 1:
                        raise RuntimeError(f"Cannot replace {name} in {path}")
            (target / path.name).write_text(text)
        # A uniform factory ensures CLI/Python instantiate the selected defaults,
        # including PBS sizing originally specified only by the portfolio.
        spec_source = (source / "backtest.py").read_text()
        tree = ast.parse(spec_source)
        spec_nodes = [
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "BACKTEST_SPEC" for t in node.targets)
        ]
        spec = ast.get_source_segment(spec_source, spec_nodes[0])
        references = {node.id for node in ast.walk(spec_nodes[0]) if isinstance(node, ast.Name)}
        constants = [
            ast.get_source_segment(spec_source, node)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and any(isinstance(t, ast.Name) and t.id in references for t in node.targets)
        ]
        spec = "\n".join([*constants, spec])
        backtest = (
            '''"""Independent backtest for the frozen optimized strategy."""
from datetime import UTC, datetime
from decimal import Decimal
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import InstrumentId
from strategy_python._backtesting import BacktestSpec, StrategyBacktest, build_argument_parser, run_backtest_cli
from strategy_python.PACKAGE import PREFIXConfig, PREFIXStrategy
INSTRUMENT_ID=InstrumentId.from_str("ETHUSDT-PERP.BINANCE")
BAR_TYPE=BarType.from_str("ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
SPEC

def create_strategy(start=None, *, bar_type=BAR_TYPE, **parameters):
    return PREFIXStrategy(PREFIXConfig(instrument_id=INSTRUMENT_ID,bar_type=bar_type,
        trade_start_ns=int(start.timestamp()*1_000_000_000) if start else 0,**parameters))

BACKTEST=StrategyBacktest("PACKAGE",BACKTEST_SPEC,create_strategy)
run_backtest=BACKTEST.run

def main():
    parser=build_argument_parser("回测已冻结的独立优化策略 PACKAGE")
    parser.set_defaults(start=datetime(2021,1,1,tzinfo=UTC),end=datetime(2026,9,1,tzinfo=UTC))
    run_backtest_cli(run_backtest,parser)

if __name__=="__main__":
    main()
'''.replace("PACKAGE", new)
            .replace("PREFIX", new_class)
            .replace("SPEC\n", spec + "\n")
        )
        (target / "backtest.py").write_text(backtest)
        # Carry existing behavioral coverage to independently copied modules.
        test_target = PROJECT / "tests/strategy_python" / new
        test_target.mkdir(parents=True, exist_ok=True)
        for path in (PROJECT / "tests/strategy_python" / old).glob("*.py"):
            test_source = path.read_text().replace(old, new).replace(old_class, new_class)
            if key in ("TFL", "TFS"):
                activation = overrides.get("take_profit_activation_return", 0.02)
                test_source = test_source.replace(
                    "assert strategy.config.take_profit_activation_return == 0.02",
                    f"assert strategy.config.take_profit_activation_return == {activation!r}",
                )
                test_source = test_source.replace(
                    "enable_two_percent_activation", "enable_selected_activation"
                )
                if path.name in ("peak_exit_test.py", "multi_position_test.py"):
                    peak = "True" if path.name == "peak_exit_test.py" else "False"
                    test_source = test_source.replace(
                        f"            peak_profit_exit={peak},",
                        f"            peak_profit_exit={peak},\n"
                        "            take_profit_activation_return=0.02,  # Explicit price-path fixture.",
                    )
            if key == "PBS" and path.name == "signal_test.py":
                test_source = test_source.replace(
                    "def test_portfolio_registry_creates_independent_member_and_rejects_fake_shared_budget():",
                    "def test_portfolio_registry_creates_independent_member_and_rejects_fake_shared_budget(monkeypatch):",
                ).replace(
                    "    from strategy_python.portfolio import create_strategies, parse_portfolio",
                    "    from strategy_python.portfolio import STRATEGY_REGISTRY, create_strategies, parse_portfolio\n"
                    f"    monkeypatch.setitem(STRATEGY_REGISTRY, {new!r}, "
                    f"({new_class}Config, {new_class}Strategy))",
                )
            (test_target / path.name).write_text(test_source)
        provenance = {
            "original_package": old,
            "selection": selections[key],
            "overrides": overrides,
            "experiments": str(ROOT.relative_to(PROJECT)),
        }
        (target / "selection.json").write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2) + "\n"
        )
        (target / "README.md").write_text(
            f"# {new}\n\n独立版本：`{new_class}Strategy`。\n\n"
            + f"本轮选择：`{selections[key]['selected']}`；相对原组合覆盖参数：`{json.dumps(overrides)}`。\n\n"
            + "依据开发期 2021–2023 年和验证期 2024 年确定；后续历史保留期不参与本轮选参。\n\n"
            + "所有领域模块均保存在本目录，不导入原策略。回测与交易使用同一个 Strategy 类。\n\n"
            + f"运行：`.venv/bin/python -m strategy_python.{new}.backtest --start 2025-01-01 --end 2026-09-01`\n\n"
            + "结果及风险限制见 `../_optimization/four_strategy_20260914/RESULTS.md`。\n"
        )
    original = json.loads(
        (
            PROJECT / "strategy_python/_portfolio/mean_reversion_long_trend_both_pullback_3x.json"
        ).read_text()
    )
    original["name"] = "four_strategies_optimized_20260914"
    for member, key in zip(original["strategies"], NAMES):
        member["strategy"] = PACKAGES[key]
        member["parameters"] = {}
    (PROJECT / "strategy_python/_portfolio/four_strategies_optimized_20260914.json").write_text(
        json.dumps(original, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
