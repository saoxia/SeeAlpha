"""按账户余额构建已实现净值曲线，生成无需联网的中文报告。"""

import json
import math
import re
from collections import defaultdict
from decimal import Decimal
from html import escape
from pathlib import Path

import pandas as pd

LABELS = {
    "by_strategy": "按策略统计", "by_currency": "按币种统计",
    "closed_positions": "已平仓周期数", "open_positions": "未平仓周期数",
    "realized_pnl_drawdown": "已实现收益曲线回撤",
    "risk_mode": "组合风控模式", "run_config": "运行清单", "config_path": "配置文件",
    "members": "成员编号映射",
    "strategies": "组合成员", "portfolio": "组合风险设置",
    "total_risk_fraction": "新周期组合风险上限", "risk_weights": "策略风险权重",
    "report_prefix": "分项报表前缀",
    "strategy_name": "策略名称", "strategy_class": "策略类", "strategy_config": "策略配置",
    "backtest_spec": "回测设置", "start": "开始时间", "end": "结束时间（不含）",
    "data_start": "预热数据开始时间", "refresh_archives": "刷新行情归档",
    "nautilus_trader_version": "回测引擎版本", "instrument_id": "交易合约",
    "data_symbol": "行情标的", "bar_type": "K 线类型", "interval": "K 线周期",
    "warmup_days": "预热天数", "oms_type": "持仓模式", "starting_balance": "初始资金",
    "leverage": "杠杆倍数", "prepare_data_before_run": "运行前准备行情",
    "mode": "交易方向", "trade_size": "交易数量", "fast_period": "快均线周期",
    "slow_period": "慢均线周期", "period": "指标周期", "std_multiplier": "标准差倍数",
    "ma_period": "均线周期", "trend_ma_period": "趋势均线周期",
    "four_hour_ma_period": "四小时均线周期", "entry_std_multiplier": "入场标准差倍数",
    "first_exit_std": "首次退出标准差倍数", "full_exit_on_first": "首次退出全部平仓",
    "short_full_exit_on_first": "空头首次退出全部平仓",
    "use_four_hour_filter": "启用四小时过滤", "trend_slope_atr_multiplier": "趋势斜率 ATR 倍数",
    "cooldown_bars": "冷静期 K 线数", "close_positions_on_stop": "停止时平仓",
    "deviation_lookback": "偏离回看周期", "trade_start_ns": "允许交易起始时间（纳秒）",
    "max_units": "最大仓位单元数", "bollinger_period": "布林带周期",
    "bollinger_std_multiplier": "布林带标准差倍数", "ma_filter_period": "均线过滤周期",
    "ma_filter_window": "均线过滤窗口", "take_profit_rate": "止盈比例",
    "max_holding_hours": "最长持仓小时数", "flat_cooldown_minutes": "平仓后冷静分钟数",
    "trader_id": "交易员编号", "strategy_id": "策略编号", "account_id": "账户编号",
    "position_id": "持仓编号", "client_order_id": "客户端订单编号",
    "venue_order_id": "交易所订单编号", "opening_order_id": "开仓订单编号",
    "closing_order_id": "平仓订单编号", "entry": "开仓方向", "side": "当前方向",
    "order_side": "买卖方向", "order_type": "订单类型", "quantity": "数量",
    "filled_qty": "成交数量", "peak_qty": "最大持仓数量", "avg_px": "平均成交价",
    "avg_px_open": "平均开仓价", "avg_px_close": "平均平仓价",
    "ts_init": "初始化时间", "ts_opened": "开仓时间", "ts_closed": "平仓时间",
    "ts_last": "最后更新时间", "ts_event": "事件时间", "duration_ns": "持仓时长（纳秒）",
    "commissions": "手续费", "realized_return": "已实现收益率",
    "realized_pnl": "已实现净收益", "is_snapshot": "历史持仓快照",
    "currency": "币种", "total": "账户余额", "free": "可用余额", "locked": "冻结余额",
    "reported": "已报告", "info": "附加信息", "type": "类型", "status": "状态",
    "time_in_force": "有效期类型", "reduce_only": "仅减仓", "post_only": "仅挂单",
    "price": "价格", "trigger_price": "触发价", "last_qty": "本次成交数量",
    "last_px": "本次成交价", "commission": "手续费", "trade_id": "成交编号",
    "total_orders": "订单总数", "total_positions": "持仓总数", "total_events": "事件总数",
    "iterations": "处理次数", "elapsed_time": "引擎耗时（秒）", "run_id": "运行编号",
    "run_started": "运行开始时间", "run_finished": "运行结束时间",
    "backtest_start": "实际数据开始时间", "backtest_end": "实际数据结束时间",
    "stats_pnls": "引擎盈亏统计", "stats_returns": "引擎收益率统计", "summary": "引擎摘要",
    "PnL (total)": "总盈亏", "PnL% (total)": "总收益率（%）",
    "Win Rate": "胜率", "Expectancy": "期望收益", "Profit Factor": "盈亏因子",
    "Returns Volatility (252 days)": "收益波动率（252 天）",
    "Sharpe Ratio (252 days)": "夏普比率（252 天）",
    "Sortino Ratio (252 days)": "索提诺比率（252 天）",
    "Returns Average": "平均收益率", "Returns Average Win": "盈利平均收益率",
    "Returns Average Loss": "亏损平均收益率", "Risk Return Ratio": "风险收益比",
    "Max Winner": "最大单笔盈利", "Avg Winner": "平均盈利", "Min Winner": "最小单笔盈利",
    "Max Loser": "最大单笔亏损", "Avg Loser": "平均亏损", "Min Loser": "最小单笔亏损",
    "trend_slope_lookback": "趋势斜率回看周期", "trend_atr_period": "趋势 ATR 周期",
    "four_hour_slope_lookback": "四小时斜率回看周期", "equity_fraction_per_side": "每侧权益比例",
    "final_exit_std": "最终退出标准差倍数", "rearm_std_multiplier": "重新入场标准差倍数",
    "ma_reentry_ratio": "均线再入场比例", "long_hard_stop_distance": "多仓硬止损距离",
    "short_hard_stop_distance": "空仓硬止损距离", "long_cycle_risk_fraction": "多仓周期风险比例",
    "short_cycle_risk_fraction": "空仓周期风险比例", "risk_move_hours": "风险近期窗口（小时）",
    "risk_reference_days": "风险参考窗口（天）", "entry_fee_rate": "入场费率",
    "forced_exit_fee_rate": "强制退出费率", "stop_cost_buffer": "止损成本缓冲",
    "recovery_ma_period": "恢复均线周期", "recovery_snapshot_bars": "恢复快照 K 线数",
    "daily_risk_minimum_release_days": "日线风险最短释放天数",
    "daily_risk_base_days": "日线风险基准天数", "daily_risk_lower_slope": "日线风险下限斜率",
    "daily_risk_upper_slope": "日线风险上限斜率", "daily_risk_coefficient": "日线风险系数",
    "instance_id": "实例编号", "machine_id": "运行机器", "run_config_id": "运行配置编号",
    "last_trade_id": "最近成交编号", "trigger_type": "触发类型", "init_id": "初始化编号",
    "expire_time_ns": "到期时间（纳秒）", "liquidity_side": "流动性角色", "slippage": "滑点",
    "is_post_only": "仅挂单", "is_reduce_only": "仅减仓", "is_quote_quantity": "按报价币计量",
    "display_qty": "展示数量", "emulation_trigger": "模拟触发方式",
    "trigger_instrument_id": "触发合约", "contingency_type": "联动类型",
    "order_list_id": "订单组编号", "linked_order_ids": "关联订单编号",
    "parent_order_id": "父订单编号", "exec_algorithm_id": "执行算法编号",
    "exec_algorithm_params": "执行算法参数", "exec_spawn_id": "执行子任务编号", "tags": "标签",
    "index": "序号", "pnl_usdt": "净收益（USDT）", "holding_hours": "持仓小时数",
    "closed_at": "平仓时间", "optimization": "优化方案", "variant": "参数方案",
    "initial_equity": "初始权益", "final_equity": "最终权益", "return_pct": "收益率（%）",
    "max_drawdown_pct_closed_trade_curve": "最大回撤（平仓曲线，%）", "trades": "平仓笔数",
    "wins": "盈利笔数", "losses": "亏损笔数", "win_rate_pct": "胜率（%）",
    "gross_profit": "盈利合计", "gross_loss": "亏损合计（绝对值）", "net_pnl": "净收益",
    "average_trade": "平均每笔收益", "average_win": "平均盈利", "average_loss_abs": "平均亏损额",
    "payoff_ratio": "平均盈亏比", "profit_factor": "盈亏因子", "best_trade": "最佳单笔收益",
    "worst_trade": "最差单笔收益", "holding_hours_all_avg": "平均持仓小时数",
    "holding_hours_all_median": "持仓小时数中位数", "holding_hours_win_avg": "盈利平均持仓小时数",
    "holding_hours_loss_avg": "亏损平均持仓小时数", "by_direction": "按方向统计",
    "long": "做多", "short": "做空",
    "order_id_tag": "订单编号标签", "use_uuid_client_order_ids": "使用 UUID 订单编号",
    "use_hyphens_in_client_order_ids": "订单编号包含连字符",
    "external_order_claims": "接管外部订单", "manage_contingent_orders": "管理联动订单",
    "manage_gtd_expiry": "管理指定日期到期", "manage_stop": "管理停止流程",
    "market_exit_interval_ms": "市价退出间隔（毫秒）",
    "market_exit_max_attempts": "市价退出最大尝试次数", "market_exit_time_in_force": "市价退出有效期",
    "market_exit_reduce_only": "市价退出仅减仓", "log_events": "记录事件",
    "log_commands": "记录指令", "log_rejected_due_post_only_as_warning": "仅挂单拒绝记录为警告",
}
VALUES = {
    "BUY": "买入", "SELL": "卖出", "LONG": "多仓",
    "SHORT": "空仓", "FLAT": "已平仓", "long_only": "仅做多", "short_only": "仅做空",
    "both": "双向", "MARKET": "市价单", "LIMIT": "限价单", "STOP_LIMIT": "止损限价单",
    "STOP_MARKET": "止损市价单", "FILLED": "已成交", "CANCELED": "已撤销",
    "NETTING": "净持仓", "HEDGING": "双向持仓",
    "MAKER": "提供流动性", "TAKER": "消耗流动性", "GTC": "撤销前有效",
    "IOC": "立即成交否则撤销", "FOK": "全部成交否则撤销", "DEFAULT": "默认",
    "NO_TRIGGER": "无触发", "NO_CONTINGENCY": "无联动", "baseline": "基准方案",
    "four_hour_filter": "四小时过滤", "slope_strength": "斜率强度过滤",
    "first_exit_at_ma": "均线处首次退出", "ma20": "MA20 方案",
    "ma20_full_first": "MA20 首次全部退出", "ma20_std_entry": "MA20 标准差入场",
}


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def timestamp(value):
    if value is None or pd.isna(value) or value in (0, "0", ""):
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        return pd.to_datetime(int(value), unit="ns", utc=True)
    return pd.to_datetime(value, utc=True)


def pnl_data(positions: pd.DataFrame, parameters: dict) -> dict:
    """保留每个完整持仓生命周期；NETTING 的历史快照也是真实已平仓交易。"""
    events = defaultdict(lambda: defaultdict(lambda: [Decimal(0), Decimal(0)]))
    trades = defaultdict(list)
    open_count = 0
    for row in positions.to_dict("records"):
        closed = timestamp(row.get("ts_closed"))
        if closed is None:
            open_count += 1
            continue
        direction = str(row.get("entry"))
        if direction not in ("BUY", "SELL"):
            raise ValueError(f"无法识别持仓开仓方向：{direction}")
        amount, currency = str(row["realized_pnl"]).rsplit(" ", 1)
        pnl = Decimal(amount.replace(",", "").replace("_", ""))
        if not pnl.is_finite():
            raise ValueError("已平仓净收益必须是有限值")
        events[currency][int(closed.timestamp() * 1000)][direction == "SELL"] += pnl
        trades[currency].append(pnl)
    start = timestamp(parameters.get("start"))
    end = timestamp(parameters.get("end"))
    all_times = [t for group in events.values() for t in group]
    lower = int(start.timestamp() * 1000) if start is not None else min(all_times, default=0)
    upper = int(end.timestamp() * 1000) if end is not None else max(all_times, default=lower + 1)
    lower = min([lower, *all_times])
    upper = max([upper, lower + 1, *all_times])
    groups = {}
    for currency in events or {"USDT": {}}:
        long = short = Decimal(0)
        points = [[lower, 0, 0, 0]]
        for time, changes in sorted(events[currency].items()):
            long += changes[0]
            short += changes[1]
            points.append([time, float(long), float(short), float(long + short)])
        points.append([upper, float(long), float(short), float(long + short)])
        values = trades[currency]
        gains = sum(v for v in values if v > 0)
        losses = -sum(v for v in values if v < 0)
        peak = drawdown = 0.0
        for point in points:
            peak = max(peak, point[3])
            drawdown = max(drawdown, peak - point[3])
        groups[currency] = {
            "points": points, "count": len(values),
            "win_rate": float(sum(v > 0 for v in values) / len(values) * 100) if values else None,
            "profit_factor": float(gains / losses) if losses else None,
            "drawdown": drawdown,
        }
    return {"groups": groups, "start": lower, "end": upper, "open_count": open_count}


def account_nav_data(account: pd.DataFrame, parameters: dict) -> dict:
    """账户 total / 初始余额；余额不包含未实现盈亏，不从持仓盈亏反推。"""
    start = timestamp(parameters.get("start"))
    end = timestamp(parameters.get("end"))
    frame = account.copy()
    if not frame.empty:
        time_column = next((c for c in ("ts_event", "Unnamed: 0") if c in frame), None)
        times = frame[time_column] if time_column else frame.index
        frame["_time"] = [timestamp(t) for t in times]
        if frame["_time"].isna().any():
            raise ValueError("账户记录缺少有效时间")
        frame = frame.sort_values("_time", kind="stable")
    if start is None:
        start = frame["_time"].min() if not frame.empty else pd.Timestamp("1970-01-01", tz="UTC")
    if end is None:
        end = frame["_time"].max() if not frame.empty else start + pd.Timedelta(days=1)
    lower = int(start.timestamp() * 1000)
    upper = max(lower + 1, int(end.timestamp() * 1000))
    groups = {}
    for currency, rows in frame.groupby("currency", sort=False) if not frame.empty else []:
        if "account_id" in rows and rows["account_id"].nunique() > 1:
            raise ValueError("同币种多个账户需要先合并余额，不能串接账户记录")
        rows = rows[rows["_time"] <= end].drop_duplicates("_time", keep="last")
        if rows.empty:
            continue
        values = [Decimal(str(v).replace(",", "").replace("_", "").split()[0])
                  for v in rows["total"]]
        if any(not v.is_finite() for v in values):
            raise ValueError("账户余额必须是有限值")
        rows = rows.assign(_balance=values)
        prior = rows[rows["_time"] <= start]
        if not prior.empty:
            initial = prior.iloc[-1]["_balance"]
        else:
            configured = parameters.get("backtest_spec", {}).get("starting_balance")
            if configured is None or frame["currency"].nunique() != 1:
                raise ValueError("缺少回测开始时的账户余额，无法计算净值")
            initial = Decimal(str(configured))
        if not initial.is_finite() or initial <= 0:
            raise ValueError("计算净值需要正的初始余额")
        points = [[lower, 1.0, float(initial)]]
        peak = initial
        drawdown = drawdown_pct = Decimal(0)
        balance = initial
        for row in rows[rows["_time"] > start].to_dict("records"):
            current = row["_balance"]
            peak = max(peak, current)
            drawdown = max(drawdown, peak - current)
            drawdown_pct = max(drawdown_pct, (peak - current) / peak * 100)
            if current != balance:
                points.append([int(row["_time"].timestamp() * 1000),
                               float(current / initial), float(current)])
            balance = current
        points.append([upper, float(balance / initial), float(balance)])
        groups[str(currency)] = {
            "points": points, "initial_balance": float(initial),
            "final_balance": float(balance), "net_pnl": float(balance - initial),
            "return_pct": float((balance / initial - 1) * 100),
            "drawdown": float(drawdown), "drawdown_pct": float(drawdown_pct),
            "count": len(rows),
        }
    return {"groups": groups, "start": lower, "end": upper}


def display(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:,.6g}" if math.isfinite(value) else "—"
    return VALUES.get(str(value), str(value))


def details(value: dict) -> str:
    rows = []
    for key, item in value.items():
        content = details(item) if isinstance(item, dict) else escape(display(item))
        rows.append(f"<tr><th>{escape(str(LABELS.get(key, key)))}</th><td>{content}</td></tr>")
    return '<table class="metrics">' + "".join(rows) + "</table>"


def report_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return '<p class="muted">暂无记录</p>'
    frame = frame.reset_index()
    # HTML 内保留最近 200 条，完整明细保存在同目录 CSV，避免多年回测生成巨型 DOM。
    frame = frame.tail(200).copy()
    for column in frame:
        if column.startswith("ts_"):
            frame[column] = frame[column].map(
                lambda v: str(timestamp(v)) if timestamp(v) is not None else "—",
            )
        else:
            frame[column] = frame[column].map(display)
    frame = frame.rename(columns=LABELS)
    return frame.to_html(index=False, escape=True, border=0, classes="records")


def write_html_report(output: Path, reports: dict, *, parameters: dict, summary: dict) -> Path:
    data = account_nav_data(reports.get("account", pd.DataFrame()), parameters)
    payload = json.dumps(json_safe(data), ensure_ascii=False, allow_nan=False)
    payload = payload.replace("<", "\\u003c").replace("&", "\\u0026")
    template = Path(__file__).with_name("template.html").read_text(encoding="utf-8")
    table_names = [("account", "账户记录"), ("fills", "成交明细"), ("positions", "持仓明细")]
    for strategy_id, member in parameters.get("strategies", {}).items():
        prefix = member.get("report_prefix", "")
        if re.fullmatch(r"strategy_\d+", prefix):
            table_names.extend((f"{prefix}_{name}", f"{strategy_id} · {title}")
                               for name, title in (("fills", "成交明细"), ("positions", "持仓明细")))
    tables = "".join(
        f'<details><summary>{escape(title)} · {len(reports.get(name, []))} 条</summary>'
        f'<p><a href="{name}.csv" download>下载完整{escape(title)} CSV</a> · 下表最多展示末尾 200 条</p>'
        f'<div class="table-wrap">{report_table(reports.get(name, pd.DataFrame()))}</div></details>'
        for name, title in table_names
        if name in reports
    )
    replacements = {
        "TITLE": escape(str(parameters.get("strategy_name", "策略"))),
        "PERIOD": escape(f'{parameters.get("start", "—")} 至 {parameters.get("end", "—")}'),
        "DATA": payload, "PARAMETERS": details(parameters), "SUMMARY": details(summary),
        "TABLES": tables,
    }
    # 单次替换，避免用户数据中的占位符被二次解释。
    html = re.sub(r"@@(\w+)@@", lambda m: replacements[m[1]], template)
    path = output / "report.html"
    temporary = output / "report.html.tmp"
    temporary.write_text(html, encoding="utf-8")
    temporary.replace(path)
    return path
