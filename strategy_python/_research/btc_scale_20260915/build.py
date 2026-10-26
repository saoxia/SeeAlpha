"""Build isolated BTC-scale packages from the frozen fixed-parameter baseline."""

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
BASE = ROOT.parent / "btc_transfer_20260914"
FIELDS = {
    "MRL": {"take_profit_rate", "hard_stop_distance"},
    "TFL": {
        "additional_entry_spacing",
        "max_adverse_hourly_ma_return",
        "max_loss_return",
        "take_profit_activation_return",
        "profit_relaxation_scale",
    },
    "TFS": {
        "additional_entry_spacing",
        "max_adverse_hourly_ma_return",
        "max_loss_return",
        "take_profit_activation_return",
        "profit_relaxation_scale",
        "daily_boll_max_width_ratio",
        "daily_boll_upper_max_width_ratio",
    },
    "PBS": {"min_stop_fraction", "max_stop_fraction"},
}


def main():
    calibration = json.loads((ROOT / "calibration.json").read_text())
    factor = calibration["factor"]
    digest = hashlib.sha256((ROOT / "calibration.json").read_bytes()).hexdigest()
    strategies = json.loads((BASE / "strategies.json").read_text())
    changes = []
    baseline = json.loads((BASE / "frozen_btc_hashes.json").read_text())
    for path, expected in baseline.items():
        assert hashlib.sha256((PROJECT / path).read_bytes()).hexdigest() == expected, path
    for key, info in strategies.items():
        old_package, old_prefix = info["package"], info["prefix"]
        info["package"] = old_package.replace("btc_", "btc_scaled_", 1)
        info["prefix"] = old_prefix.replace("Btc", "BtcScaled", 1)
        target = PROJECT / "strategy_python" / info["package"]
        target.mkdir(exist_ok=True)
        for source in (PROJECT / "strategy_python" / old_package).glob("*.py"):
            code = (
                source.read_text()
                .replace(old_package, info["package"])
                .replace(old_prefix, info["prefix"])
            )
            scale_import = f"from strategy_python.{info['package']}.scale import BTC_PRICE_SCALE\n"
            if source.name == "config.py":
                lines = code.splitlines()
                for node in ast.walk(ast.parse(code)):
                    if (
                        isinstance(node, ast.AnnAssign)
                        and isinstance(node.target, ast.Name)
                        and node.target.id in FIELDS[info["family"]]
                    ):
                        original = ast.literal_eval(node.value)
                        if original is None:
                            continue
                        lines[node.lineno - 1] += (
                            " * BTC_PRICE_SCALE  # BTC波动尺度；上方注释数值为ETH来源值"
                        )
                        changes.append(
                            {
                                "strategy": key,
                                "field": node.target.id,
                                "source_value": original,
                                "btc_value": original * factor,
                            }
                        )
                code = "\n".join(lines) + "\n"
                code = code.replace(
                    "from nautilus_trader.config", scale_import + "\nfrom nautilus_trader.config", 1
                )
            if source.name == "risk.py" and info["family"] == "MRL":
                code = code.replace(
                    "from collections import deque",
                    scale_import + "\nfrom collections import deque",
                    1,
                )
                code = code.replace(
                    "((2, 0.005), (3, 0.01), (4, 0.02))",
                    "((2, 0.005 * BTC_PRICE_SCALE), (3, 0.01 * BTC_PRICE_SCALE), (4, 0.02 * BTC_PRICE_SCALE))",
                )
                code = code.replace(
                    "stop_distance: float = 0.25", "stop_distance: float = 0.25 * BTC_PRICE_SCALE"
                )
                for level, value in ((2, 0.005), (3, 0.01), (4, 0.02)):
                    changes.append(
                        {
                            "strategy": key,
                            "field": f"add_offsets.level_{level}",
                            "source_value": value,
                            "btc_value": value * factor,
                        }
                    )
            if source.name == "profit_protection.py":
                code = code.replace(
                    "from math import isfinite, sqrt",
                    "from math import isfinite, sqrt\n\n" + scale_import,
                )
                code = code.replace(
                    "    progress =",
                    "    # 将BTC浮盈还原到ETH来源曲线的尺度；锁利比例与曲线形状不变。\n    peak_return /= BTC_PRICE_SCALE\n    progress =",
                    1,
                )
                code = code.replace("1.55% 以下", "换算到ETH尺度后，1.55% 以下")
                for name, value in (("dynamic_lock_lower", 0.0155), ("dynamic_lock_upper", 0.035)):
                    changes.append(
                        {
                            "strategy": key,
                            "field": name,
                            "source_value": value,
                            "btc_value": value * factor,
                        }
                    )
            if source.name == "strategy.py":
                code = code.replace(
                    "        self._validate_config(config)",
                    "        self._validate_config(config)",
                    1,
                )
                marker = "        super().__init__(config)"
                assert marker in code
                code = code.replace(
                    marker,
                    '        if config.trade_start_ns < 1609459200000000000:\n            raise ValueError("BTC尺度使用2020年校准数据；trade_start_ns必须不早于2021-01-01 UTC")\n'
                    + marker,
                    1,
                )
            if source.name == "backtest.py":
                code = code.replace("保留来源策略默认交易参数", "使用2020年统计冻结的BTC波动尺度")
                code = code.replace(" if start else 0,", " if start else 1609459200000000000,")
            (target / source.name).write_text(code)
        (target / "scale.py").write_text(
            f'"""2020年同期完整日的BTC/ETH已实现波动比中位数；非收益选参。"""\n\nBTC_PRICE_SCALE = {factor!r}\nCALIBRATION_SHA256 = "{digest}"\n'
        )
        (target / "README.md").write_text(
            f"# {info['package']}\n\nBTC波动尺度适配版，对应 {old_package}。\n\n[换算公式、限制及参数表](../BTC_SCALE.md) · [参数来源](PARAMETER_SOURCES.md)\n\n独立入口：`python -m strategy_python.{info['package']}.backtest --start 2022-05-01 --end 2022-06-01`。\n\n默认交易起点2021-01-01 UTC；更早起点会拒绝，避免使用尚未完成的2020年校准统计。调用配置时必须设置 trade_start_ns。外部传入覆盖值按BTC单位解释，不会再次换算。\n"
        )
        (target / "PARAMETER_SOURCES.md").write_text(
            f"# 参数来源\n\n[原始逐项审计](../{old_package}/PARAMETER_SOURCES.md)保留ETH参数的真实来源。\n\n本版本价格收益阈值乘固定系数 `{factor!r}`；配置中上方原注释的数值指ETH来源值，实际默认值以表达式为准。\n\n[完整换算清单及统计依据](../BTC_SCALE.md)。原始经验参数不会因为尺度换算而成为统计定律。\n"
        )
    (ROOT / "strategies.json").write_text(json.dumps(strategies, indent=2) + "\n")
    (ROOT / "parameter_changes.json").write_text(json.dumps(changes, indent=2) + "\n")
    runner = (
        (BASE / "run.py")
        .read_text()
        .replace(
            '"""Fixed-parameter BTC transfer, native engine, bounded-memory continuous batches."""',
            '"""BTC scale evaluation; isolated outputs, native engine, continuous streaming."""',
        )
    )
    (ROOT / "run.py").write_text(runner)
    print("BUILT", len(strategies), "packages", len(changes), "scaled values")


if __name__ == "__main__":
    main()
