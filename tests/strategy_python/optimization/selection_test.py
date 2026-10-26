"""Guard selection boundaries and prevent implicit holdout tuning."""

import json

from strategy_python._research.four_strategy_20260914 import select as selection


def write_trial(root, period, name, **changes):
    metrics = {
        "net_pnl": 100,
        "max_drawdown_pct": 10,
        "trades": 30,
        "annual_marked_pnl": {"2021": 40, "2022": 40, "2023": 20},
    }
    metrics.update(changes)
    path = root / "runs" / "TFL" / period / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "metrics.json").write_text(json.dumps(metrics))


def test_validation_return_and_drawdown_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(selection, "ROOT", tmp_path)
    for period in ("train", "validation"):
        write_trial(tmp_path, period, "baseline")
        write_trial(
            tmp_path,
            period,
            "good",
            net_pnl=120,
            annual_marked_pnl={"2021": 50, "2022": 45, "2023": 25},
        )
        write_trial(tmp_path, period, "unsafe", net_pnl=400, max_drawdown_pct=30)
    assert selection.select("TFL")["selected"] == "good"


def test_no_eligible_improvement_keeps_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(selection, "ROOT", tmp_path)
    for period in ("train", "validation"):
        write_trial(tmp_path, period, "baseline")
    write_trial(tmp_path, "train", "overfit", net_pnl=200)
    write_trial(tmp_path, "validation", "overfit", net_pnl=90)
    assert selection.select("TFL")["selected"] == "baseline"


def test_unseen_results_cannot_change_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(selection, "ROOT", tmp_path)
    for period in ("train", "validation"):
        write_trial(tmp_path, period, "baseline")
        write_trial(
            tmp_path,
            period,
            "good",
            net_pnl=120,
            annual_marked_pnl={"2021": 50, "2022": 45, "2023": 25},
        )
    before = selection.select("TFL")
    write_trial(tmp_path, "holdout", "good", net_pnl=-500)
    write_trial(tmp_path, "full", "good", net_pnl=-500)
    assert selection.select("TFL") == before
