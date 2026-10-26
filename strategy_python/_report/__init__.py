"""报告生成与统一产物目录。"""

import re
from pathlib import Path as Path

from core.paths import PROJECT_ROOT
from strategy_python._report.report import write_html_report as write_html_report


def backtest_report_dir(name: str, *, portfolio: bool = False) -> Path:
    """单策略与多策略产物分区，避免同名覆盖。"""
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError("报告名称只能包含字母、数字和下划线")
    root = PROJECT_ROOT / "data/reports/backtest"
    return root / "portfolio" / name if portfolio else root / name


__all__ = ["backtest_report_dir", "write_html_report"]
