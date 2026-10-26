import json
import re

import pandas as pd
import pytest

from strategy_python._report.report import account_nav_data, pnl_data, write_html_report

PARAMETERS = {"strategy_name": "测试策略", "start": "2025-01-01", "end": "2026-09-01"}


def position(entry, pnl, closed, **kwargs):
    return {"entry": entry, "realized_pnl": pnl, "ts_closed": closed, **kwargs}


def test_curve_sorts_groups_sides_and_keeps_closed_snapshots():
    positions = pd.DataFrame([
        position("SELL", "-2.25 USDT", "2026-08-01"),
        position("BUY", "10.10 USDT", "2025-01-02", is_snapshot=True),
        position("BUY", "1.15 USDT", "2026-08-01"),
        position("SELL", "500 USDT", None),
    ])
    result = pnl_data(positions, PARAMETERS)
    group = result["groups"]["USDT"]
    assert group["points"][0][1:] == [0, 0, 0]
    assert group["points"][1][1:] == [10.1, 0, 10.1]
    assert group["points"][-1][1:] == [11.25, -2.25, 9.0]
    assert group["count"] == 3
    assert group["drawdown"] == pytest.approx(1.1)
    assert group["win_rate"] == pytest.approx(200 / 3)
    assert result["open_count"] == 1
    for point in group["points"]:
        assert point[3] == pytest.approx(point[1] + point[2])


def test_currencies_are_never_added_together():
    result = pnl_data(pd.DataFrame([
        position("BUY", "1.25 USDT", "2026-01-01"),
        position("SELL", "0.001 BTC", "2026-01-01"),
    ]), PARAMETERS)
    assert result["groups"]["USDT"]["points"][-1][3] == 1.25
    assert result["groups"]["BTC"]["points"][-1][3] == .001


def test_no_closed_positions_has_zero_curve_and_no_fake_statistics():
    data = pnl_data(pd.DataFrame(), PARAMETERS)
    group = data["groups"]["USDT"]
    assert group["count"] == 0
    assert group["win_rate"] is None
    assert all(p[1:] == [0, 0, 0] for p in group["points"])


def test_report_is_offline_and_escapes_user_content(tmp_path):
    attack = '</script><script>alert("unsafe")</script>@@DATA@@'
    parameters = {**PARAMETERS, "strategy_name": attack}
    path = write_html_report(tmp_path, {}, parameters=parameters, summary={"Win Rate": float("nan")})
    html = path.read_text()
    assert attack not in html
    assert '&lt;/script&gt;' in html
    assert '<script src=' not in html
    assert '近 6 个月' in html and '全部' in html
    assert '账户已实现净值' in html and '最终余额' in html
    assert '胜率' in html
    payload = re.search(r'<script id="pnl-data" type="application/json">(.*?)</script>', html)[1]
    assert json.loads(payload)["groups"] == {}


def test_invalid_pnl_does_not_silently_drop_trades():
    with pytest.raises(ValueError, match="有限值"):
        pnl_data(pd.DataFrame([position("BUY", "NaN USDT", "2026-01-01")]), PARAMETERS)


def test_nav_uses_account_balances_keeps_currency_separate_and_excludes_warmup():
    frame = pd.DataFrame({
        "currency": ["USDT"] * 5 + ["BTC"] * 2,
        "total": [500, 500, 600, 300, 450, 2, 3],
    }, index=pd.to_datetime([
        "2024-12-01", "2025-01-01", "2025-02-01", "2025-03-01", "2025-04-01",
        "2024-12-01", "2025-02-01",
    ], utc=True))
    data = account_nav_data(frame, PARAMETERS)
    g = data["groups"]["USDT"]
    assert g["points"][0] == [data["start"], 1, 500]
    assert g["points"][-1] == [data["end"], .9, 450]
    assert g["net_pnl"] == -50
    assert g["drawdown"] == 300
    assert g["drawdown_pct"] == 50
    assert g["return_pct"] == -10
    assert data["groups"]["BTC"]["points"][-1][1:] == [1.5, 3]
    assert min(p[0] for p in g["points"]) == data["start"]


def test_nav_csv_duplicate_timestamps_and_idle_tail():
    frame = pd.DataFrame({
        "Unnamed: 0": ["2025-01-01", "2025-03-01", "2025-03-01", "2026-10-01"],
        "currency": ["USDT"] * 4, "total": [500, 400, .26595982, 900],
    })
    g = account_nav_data(frame, PARAMETERS)["groups"]["USDT"]
    assert g["final_balance"] == .26595982
    assert g["net_pnl"] == pytest.approx(-499.73404018)
    assert g["points"][-1][1] == pytest.approx(.26595982 / 500)
    assert len(g["points"]) == 3


def test_html_curve_ignores_incorrect_position_pnl(tmp_path):
    reports = {
        "positions": pd.DataFrame([position("SELL", "108.85043853 USDT", "2025-03-01")]),
        "account": pd.DataFrame({"total": [500, .26595982], "currency": ["USDT"] * 2},
                                index=pd.to_datetime(["2025-01-01", "2025-03-01"], utc=True)),
    }
    html = write_html_report(tmp_path, reports, parameters=PARAMETERS, summary={}).read_text()
    payload = re.search(r'<script id="pnl-data" type="application/json">(.*?)</script>', html)[1]
    g = json.loads(payload)["groups"]["USDT"]
    assert g["net_pnl"] == pytest.approx(-499.73404018)
    assert g["final_balance"] == .26595982


def test_nav_no_trades_stays_at_one_and_invalid_balance_rejected():
    frame = pd.DataFrame({"total": [500], "currency": ["USDT"]},
                         index=pd.to_datetime(["2024-12-01"], utc=True))
    g = account_nav_data(frame, PARAMETERS)["groups"]["USDT"]
    assert all(p[1] == 1 for p in g["points"])
    assert g["drawdown_pct"] == 0
    frame["total"] = float("nan")
    with pytest.raises(ValueError, match="有限值"):
        account_nav_data(frame, PARAMETERS)
