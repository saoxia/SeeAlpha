"""Read-only source/experiment audit; does not run or modify trading strategies."""

import ast
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
EXP = ROOT / "strategy_python/_optimization/four_strategy_20260914"
NAMES = {
    "MRL": ("mean_reversion_long_5min", "mean_reversion_long_optimized_5min"),
    "TFL": ("trend_following_long_5min", "trend_following_long_optimized_5min"),
    "TFS": ("trend_following_short_5min", "trend_following_short_optimized_5min"),
    "PBS": ("pullback_short_1h", "pullback_short_optimized_1h"),
}

# Meaning, default activity, provenance, falsifiable statistical interpretation.
META = {}


def group(keys, meaning, activity, provenance, validation):
    for key in keys.split():
        META[key] = (meaning, activity, provenance, validation)


group("instrument_id bar_type trade_start_ns order_id_tag close_positions_on_stop",
      "合约/输入周期/交易开始时间/订单隔离/停止清仓的运行配置（按字段名区分）",
      "运行设置", "工程约定；不是由市场统计估计",
      "跨资产替换合约和输入；保证相同交易边界及末尾清仓口径")
group("max_units", "最多4个等数量仓位单元", "启用", "MRL交接默认；未找到原始选参记录",
      "按整个加仓周期估计尾部损失；不能把4层当作4个独立样本")
group("bollinger_period bollinger_std_multiplier", "5分钟收盘SMA与总体标准差的下轨：均值-k×标准差",
      "启用", "周期20和原k=2来自交接；新k=2.25来自本轮筛选",
      "共同候选时点分层比较折价、触及率、回归时间及加仓周期净收益；检验邻域稳定性")
group("ma_filter_period ma_filter_window ma_reentry_ratio", "15分钟low<当根SMA99的最近54次比例≤0.65才准入",
      "启用", "MRL交接默认；本轮仅测试比例0.45/0.55且未选中",
      "分箱估计不利状态比例与未来净收益/尾部损失关系；54次有重叠，不能当独立伯努利样本")
group("take_profit_rate", "≤2单元在均价上方0.5%全平；>2单元先在均价减半",
      "启用", "MRL交接默认；本轮0.3%/0.75%/1%候选未选中",
      "比较条件回归幅度和到达时间分布及净成本；0.5%不是统计均值的自动推导")
group("max_holding_hours", "MRL限制加仓窗口；PBS限制实际持仓时间", "启用",
      "MRL48小时继承；PBS24小时来自既往候选筛选并偏好较短暴露",
      "MRL估计不同持仓年龄继续加仓的边际收益；PBS估计尚未退出头寸继续持有的条件价值")
group("flat_cooldown_minutes", "MRL止盈全平后等待5分钟", "启用", "交接默认；原始依据未找到",
      "比较全平后不同等待时间的再入场净收益及重复噪声")
group("hard_stop_distance", "MRL按持仓均价下方25%止损", "启用", "交接风险设计；未找到尾部分位数推导",
      "按周期最大不利变动与实际缺口损失评估；价格跌幅25%不是权益风险25%")
group("cycle_risk_fraction", "MRL基础周期风险30%，再乘强度/位置/恢复系数", "启用",
      "风险预算选择；无统计最优证明", "先明确风险容忍度；检验复利回撤和尾部，不能只最大化历史收益")
group("risk_move_hours risk_reference_days", "312小时近期方向最大跌幅 / 向前错开312小时的120天参考最大跌幅",
      "启用", "计算公式可复现；窗口312/120原始依据未找到",
      "比较不同回溯长度下预测尾部风险的校准；样本最大值不是分位数")
group("entry_fee_rate forced_exit_fee_rate stop_cost_buffer", "MRL定仓成本假设：入场2bp、强退5bp、总风险再乘1.05",
      "启用", "成本/风险缓冲设定；不等于引擎实际逐笔费率",
      "用实际手续费档位、成交类型和不利成交误差分布校准；换资产要核对")
group("recovery_ma_period", "止损后6小时及日线MA52恢复检查", "启用",
      "MRL交接默认；未找到选参样本", "以硬止损事件为样本，比较恢复等待时间与后续周期损失")
group("recovery_snapshot_bars", "保存最近26根6小时K线并在首仓记录快照", "仅记录",
      "当前源码未发现快照被交易决策读取", "无需为26拟合收益解释；后续若接入决策需重新审计")
group("daily_risk_minimum_release_days daily_risk_base_days daily_risk_lower_slope daily_risk_upper_slope daily_risk_coefficient",
      "若释放等待d≥7天且未日线确认，在止损后[26+1.5d,26+1.8d]天将风险乘0.30",
      "条件启用", "MRL交接规则；未找到回归样本/拟合记录",
      "先检验该时段是否有额外风险；对比无窗口/简单单调恢复，禁止先画窗口再只检验窗口内")
group("max_positions_per_side equity_fraction_per_side", "每策略最多1份；每份名义金额为账户权益1倍",
      "启用", "仓位预算设计；并非预测收益参数", "统一风险后比较信号；不可与账户5倍保证金杠杆混淆")
group("additional_entry_spacing", "多份持仓时，新均价距其他均价严格超过4%", "默认单份下不参与决策",
      "遗留实验设置；原始依据未找到", "只有启用多份时才检验，需与波动率及相关风险联合分析")
group("ma_period", "趋势：5分钟MA20；PBS：小时SMA20", "启用/部分间接",
      "趋势默认原始依据未找到；PBS在MA20/MA99结构候选中选20",
      "以实际时间尺度比较平滑延迟与信号净收益；趋势空头仍有指标预热依赖")
group("entry_ma_period deviation_lookback", "入场偏离模型SMA99和52根最大高低偏离；交叉模式仍要求模型就绪",
      "默认非入场价公式，但有预热依赖", "继承默认；未找到原始选参记录",
      "勿当成默认突破规则；隔离预热差异后才评估该参数的经济作用")
group("entry_trigger cross_fast_period cross_slow_period", "完整5分钟MA52/MA99交叉；entry_trigger=ma_cross",
      "启用", "继承默认；本轮快线26/65候选未选中，慢线99固定",
      "比较趋势延续与假交叉成本，检验等时间尺度的宽邻域而非单个最优点")
group("trend_ma_period trend_slope_lookback entry_on_slope_change max_adverse_hourly_ma_return",
      "小时MA20；当小时斜率与6小时前比较；多头小时变化率>-0.1%，空头默认未设上限",
      "多头过滤启用；空头过滤关闭但仍要求小时指标就绪", "继承默认；原始选参记录未找到",
      "多头检验斜率变化而非仅斜率为正；空头小时过滤关闭后不可据此解释收益")
group("require_entry_cross require_hourly_price_filter require_hourly_slope_filter require_reentry_zone",
      "空头死叉必需；小时价格、小时斜率和重入区默认不要求", "按开关值",
      "README记录取消第2/3/4/7条；本地未找到完整原始消融结果",
      "复核消融矩阵及各市场阶段的边际贡献；这是结构自由度而非几个数值参数")
group("daily_boll_entry_filter daily_boll_only_entry daily_boll_period daily_boll_std",
      "TFS两日日线低点过滤（含动态当日）；PBS仅最近完整日线弱势过滤；20期2σ",
      "日线过滤启用；TFS仅日线模式关闭", "TFS继承；PBS固定背景设计，未证明20/2统计最优",
      "区分低点穿轨与收盘穿轨；不得把2σ当固定尾概率；TFS动态日线必须逐时重建")
group("daily_boll_max_width_ratio daily_boll_upper_max_width_ratio",
      "空头日线带宽/位置可选例外和上轨限制", "None，关闭", "遗留实验参数",
      "当前不贡献收益；若开启需独立验证，不能沿用默认版本结论")
group("peak_profit_exit peak_profit_lock dynamic_peak_profit_lock", "默认峰值止盈开启，固定锁住最大毛浮盈65%；动态锁利关闭",
      "固定锁利启用", "65%继承；本轮50%/80%候选未作为最终更改",
      "以触及启动点后的回吐比例分布和净期望检验；65%不是胜率或成交收益保证")
group("max_loss_return native_stop_loss", "初始价格止损0.5%；原生止损市价单开启", "启用",
      "继承风险设计；本轮固定", "比较入场后最大不利变动、止损重入频度及成本；不能从3%/4%反推其最优")
group("take_profit_activation_return", "原2%；新版趋势多3%、趋势空4%毛浮盈启动锁利", "启用",
      "本轮开发初筛+2024验证选择；原2%原始依据未找到",
      "共同入场机会重放价格路径，比较启动概率与启动后的回吐/延续；需防旧止盈截断观察")
group("exit_on_reverse_cross exit_on_ma99 exit_ma_period exit_on_ma_slope_reversal",
      "反向交叉/MA99/斜率反向的可选退出，MA周期99", "开关关闭，默认峰值分支不使用",
      "遗留实验设置", "默认结果不能支持这些分支；打开后属于另一策略")
group("first_exit_std final_exit_std long_full_exit_on_first short_full_exit_on_first",
      "可选MA退出：首线顺向0.2σ，末线逆向0.3σ；首线全平开关", "默认峰值退出下不作退出判断",
      "遗留实验设置；原始依据未找到", "若改用MA退出，再分析回归与延续路径；当前不能拿这两个值解释利润")
group("tighten_exits_with_slope_risk slope_risk_ema_period slope_risk_scale slope_risk_max_tightening_std slope_risk_rule slope_risk_price_buffer_std slope_risk_long_multiplier slope_risk_short_multiplier profit_relaxes_slope_risk profit_relaxation_scale",
      "可选标准化斜率收紧退出及浮盈放松；算法见risk.py", "默认不用于退出，部分指标仍计算",
      "遗留实验参数；未找到统计拟合记录", "先验证该分支有增量信息，再校准尺度；当前默认业绩不支持其数值")
group("cooldown_bars stop_loss_cooldown_seconds", "全平后K线冷却与止损后秒数冷却，默认均0", "关闭",
      "继承；本轮仅试止损后3600秒且未选中", "按止损事件估计再入场机会成本及连续亏损相关性")
group("rearm_std_multiplier", "回到5分钟MA20±0.2σ才重新获入场资格", "多头启用；空头不要求",
      "继承默认；原始依据未找到", "检验重入区内外下一次有效交叉表现；数值边界与σ算法共同固定")
group("atr_period swing_period stop_atr_buffer", "PBS小时ATR14算术均值；12根最高价+0.25ATR为结构止损",
      "启用", "原研究中14/12/0.25固定；本轮结构6/24未选中",
      "统计反弹后越过结构高点的噪声幅度，检验ATR缓冲与尾部逃逸，不可称原值为已估计分位数")
group("reward_risk", "PBS止盈目标=实际入场价-4×实际结构风险", "启用",
      "原294次结构筛选中的4R；邻近2.5R/6R也盈利；本轮保留",
      "比较先达止盈概率及时间退出混合收益；理论20%盈亏平衡仅适用无成本且只±1R/4R退出")
group("risk_fraction max_notional_fraction", "PBS原类0.5%/1倍；原组合和新类1.5%/3倍", "启用",
      "旧组合覆盖；既往3倍仓位对照；不是本轮Alpha改进", "固定同一风险预算比较新旧；风险偏好不能伪装成统计规律")
group("min_stop_fraction max_stop_fraction", "PBS止损距信号收盘需在0.4%～3%", "启用",
      "原研究固定风控门槛；未找到这两值的拟合记录", "估计过窄噪声止损/过宽结构的条件净收益；跨资产可用波动归一化候选检验")
group("fee_buffer", "PBS每单位风险加(入场价+止损价)×0.0007", "启用",
      "定仓成本缓冲；不是实际手续费扣款", "按市场实际费率和成交误差估计；不能只改参数便称回测已含滑点")

# Individual field definitions retain the grouped provenance and validation context.
DEFINITIONS = {
    "instrument_id": "交易合约标识；本轮ETHUSDT-PERP.BINANCE",
    "bar_type": "行情类型；本轮外部1分钟LAST K线",
    "trade_start_ns": "允许交易的UTC纳秒时间戳；工厂用回测开始时间覆盖0",
    "order_id_tag": "订单编号标签，用于隔离策略订单",
    "close_positions_on_stop": "策略停止时清仓开关；不是持仓时长阈值",
    "bollinger_period": "5分钟BOLL收盘样本数20，即100分钟",
    "bollinger_std_multiplier": "5分钟BOLL下轨的总体标准差倍数k",
    "ma_filter_period": "15分钟均线收盘样本数99，即24小时45分钟",
    "ma_filter_window": "统计low<当根MA99的15分钟状态数54",
    "ma_reentry_ratio": "允许首仓的不利状态比例上限；0.65等价于54次中至多35次",
    "risk_move_hours": "方向最大跌幅及区间位置的近期窗口312小时；同时用于参考窗口错开",
    "risk_reference_days": "参考方向最大跌幅的历史窗口120天，不含近期312小时",
    "entry_fee_rate": "定仓预算中每笔入场成本比例0.0002",
    "forced_exit_fee_rate": "定仓预算中止损名义金额成本比例0.0005",
    "stop_cost_buffer": "计划止损价格损失及费用合计再乘(1+0.05)",
    "daily_risk_minimum_release_days": "释放等待d至少7天才可能触发额外恢复风险窗口",
    "daily_risk_base_days": "止损后风险窗口下/上界共同截距26天",
    "daily_risk_lower_slope": "下界=26+1.5×释放等待天数",
    "daily_risk_upper_slope": "上界=26+1.8×释放等待天数",
    "daily_risk_coefficient": "满足日线恢复风险窗口时将周期风险预算乘0.30",
    "max_positions_per_side": "本方向策略最多独立持仓份数1",
    "equity_fraction_per_side": "每份入场名义金额/账户盯市权益=1.0",
    "entry_ma_period": "突破偏离模型的5分钟SMA周期99；交叉默认仍检查其就绪",
    "deviation_lookback": "历史上下最大偏离的5分钟回溯52根；影响模型就绪",
    "entry_trigger": "ma_cross采用均线交叉市价入场；breakout为可选突破分支",
    "cross_fast_period": "5分钟快均线52根=4小时20分钟",
    "cross_slow_period": "5分钟慢均线99根=8小时15分钟",
    "trend_ma_period": "小时均线20根=20小时",
    "trend_slope_lookback": "比较当前小时MA斜率与6小时前斜率（默认模式）",
    "entry_on_slope_change": "True使用斜率变化；False使用均线水平变化",
    "max_adverse_hourly_ma_return": "小时MA单步逆向变动上限；多头0.001，空头None关闭",
    "require_entry_cross": "空头是否必须出现新的5分钟死叉；默认True",
    "require_hourly_price_filter": "空头是否要求小时收盘低于小时MA；默认False",
    "require_hourly_slope_filter": "空头是否要求小时斜率方向条件；默认False",
    "require_reentry_zone": "空头全平后是否必须观察重入区；默认False",
    "daily_boll_entry_filter": "空头两日日线低点BOLL过滤开关；默认True",
    "daily_boll_only_entry": "空头只保留两日日线低点建仓条件的对照模式；默认False",
    "daily_boll_period": "日线BOLL收盘样本数20",
    "daily_boll_std": "日线BOLL总体标准差倍数2.0",
    "daily_boll_max_width_ratio": "可选按日线带宽及价格位置限定两日低点过滤适用范围；None关闭",
    "daily_boll_upper_max_width_ratio": "可选在价格位于上轨以上且带宽过大时禁止建仓；None关闭",
    "peak_profit_exit": "True使用峰值浮盈保护；False切换可选MA退出",
    "peak_profit_lock": "固定锁定峰值毛浮盈的比例0.65",
    "dynamic_peak_profit_lock": "用动态曲线替代固定锁利比例；默认False",
    "max_loss_return": "相对实际入场价格初始止损距离0.005",
    "native_stop_loss": "True挂原生止损单；并非只在策略收盘判断止损",
    "exit_on_reverse_cross": "可选MA退出分支中按反向交叉退出；默认False",
    "exit_on_ma99": "可选用专门MA退出线；默认False",
    "exit_ma_period": "专门退出均线周期99；对应开关默认关闭",
    "exit_on_ma_slope_reversal": "可选按MA斜率反向退出；默认False",
    "first_exit_std": "MA退出首线沿持仓方向偏离均线0.2σ",
    "final_exit_std": "MA退出末线沿持仓反方向偏离均线0.3σ",
    "long_full_exit_on_first": "多头可选MA退出首线触及即全平开关；默认True",
    "short_full_exit_on_first": "空头可选MA退出首线触及即全平开关；默认True",
    "tighten_exits_with_slope_risk": "可选按标准化斜率收紧退出；默认False",
    "slope_risk_ema_period": "标准化斜率EMA周期1，alpha=2/(1+1)=1",
    "slope_risk_scale": "斜率风险映射尺度0.1：clip(0.5-顺向标准化斜率/(2×scale),0,1)",
    "slope_risk_max_tightening_std": "fixed规则最多收紧1.5σ再乘风险/方向倍率",
    "slope_risk_rule": "fixed按标准差定移位；distance按距离现价的可用空间",
    "slope_risk_price_buffer_std": "distance规则保留现价0.1σ缓冲",
    "slope_risk_long_multiplier": "多头斜率收紧倍率1.0",
    "slope_risk_short_multiplier": "空头斜率收紧倍率1.0",
    "profit_relaxes_slope_risk": "可选随正浮盈降低额外收紧强度；默认False",
    "profit_relaxation_scale": "放松系数1/(1+正浮盈/0.01)的尺度",
    "cooldown_bars": "平仓后固定K线冷却根数0",
    "stop_loss_cooldown_seconds": "止损后冷却秒数0",
    "atr_period": "小时真实波幅算术均值样本数14",
    "swing_period": "小时结构最高价窗口12根",
    "stop_atr_buffer": "结构最高价上方额外0.25×ATR",
    "risk_fraction": "PBS单笔计划风险/权益：旧类0.005，旧组合和新类0.015",
    "max_notional_fraction": "PBS名义金额/权益上限：旧类1，旧组合和新类3",
    "min_stop_fraction": "结构止损距信号收盘的比例下限0.004",
    "max_stop_fraction": "结构止损距信号收盘的比例上限0.03",
}
for name, meaning in DEFINITIONS.items():
    META[name] = (meaning, *META[name][1:])


def config_fields(path):
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    return [(n.target.id, ast.literal_eval(n.value) if n.value else "<required>", n.lineno)
            for n in cls.body if isinstance(n, ast.AnnAssign)]


def csv_write(name, rows):
    with (OUT / name).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    inventory, changes, hashes = [], [], {}
    for key, packages in NAMES.items():
        defaults = []
        for version, package in zip(("原版", "新版"), packages):
            directory = ROOT / "strategy_python" / package
            for path in sorted(directory.glob("*.py")):
                hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
            path = directory / "config.py"
            fields = config_fields(path)
            defaults.append(dict((k, v) for k, v, _ in fields))
            for name, value, line in fields:
                meaning, activity, provenance, validation = META[name]
                effective = value
                if key == "PBS" and name in ("risk_fraction", "max_notional_fraction"):
                    effective = {"risk_fraction": 0.015, "max_notional_fraction": 3.0}[name]
                inventory.append(dict(strategy=key, version=version, package=package,
                    parameter=name, class_default=json.dumps(value, ensure_ascii=False),
                    comparison_value=json.dumps(effective, ensure_ascii=False),
                    meaning=meaning, default_activity=activity, evidence=provenance,
                    statistical_test_needed=validation, source=f"{path.relative_to(ROOT)}:{line}"))
        for name, value in defaults[0].items():
            if value != defaults[1][name]:
                changes.append(dict(strategy=key, parameter=name, old=value, new=defaults[1][name]))
    csv_write("参数逐项审计.csv", inventory)
    (OUT / "config_differences.json").write_text(json.dumps(changes, ensure_ascii=False, indent=2)+"\n")
    (OUT / "source_hashes.json").write_text(json.dumps(hashes, indent=2)+"\n")

    trials = []
    for stage in ("screen_5m", "screen_5m_round2", "runs"):
        for path in sorted((EXP / stage).glob("*/[tv]*/*/metrics.json")):
            key, period, name = path.parts[-4:-1]
            if period not in ("train", "validation"):
                continue
            m = json.loads(path.read_text())
            selected = json.loads((EXP / f"{key}_selection.json").read_text())["selected"]
            trials.append(dict(stage=stage, strategy=key, period=period, candidate=name,
                selected=name == selected, net_pnl=m["net_pnl"], max_drawdown_pct=m["max_drawdown_pct"],
                trades=m["trades"], source=str(path.relative_to(ROOT))))
    csv_write("既有候选证据.csv", trials)
    lines = ["# 原生分钟级候选证据", "", "仅汇总已存在的开发/验证结果；未新增回测。收益单位USDT，回撤单位%。", ""]
    for key in NAMES:
        lines += [f"## {key}", "", "| 候选 | 开发净收益 | 验证净收益 | 验证回撤 | 开发/验证笔数 | 最终选中 |", "|---|---:|---:|---:|---:|---|"]
        rs = [r for r in trials if r["stage"] == "runs" and r["strategy"] == key]
        for name in sorted(set(r["candidate"] for r in rs)):
            tr = next(r for r in rs if r["candidate"] == name and r["period"] == "train")
            va = next(r for r in rs if r["candidate"] == name and r["period"] == "validation")
            lines.append(f'| {name} | {tr["net_pnl"]:.2f} | {va["net_pnl"]:.2f} | {va["max_drawdown_pct"]:.2f} | {tr["trades"]}/{va["trades"]} | {"是" if tr["selected"] else ""} |')
        lines.append("")
    (OUT / "候选选择证据.md").write_text("\n".join(lines)+"\n")
    print(json.dumps(dict(parameter_rows=len(inventory), source_files=len(hashes),
                         candidate_metric_rows=len(trials), config_changes=changes), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
