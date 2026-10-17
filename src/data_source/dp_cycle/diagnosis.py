# -*- coding: utf-8 -*-
"""大盘位置诊断。

规则来自 research/DP/大周期/框架.md。
只给已经有序列的项打分。缺数据的项列入缺口，不记成 0 分。
作者 2026-06 第四周的 -9 还包含公募新发、减持、行业资金等，这里复刻不了。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# 口播里的数量级，用来对照，不是拟合出来的最优阈值。
DIVERGENCE_EXTREME = 16.0
HL_HOT_RATIO = 8.0
HL_COLD_RATIO = 0.2
UNLOCK_HOT_MULT = 1.5
MAIN_FORCE_WEEK_EXTREME = -2000.0

# 融资：看仓位历史分位 × 近月方向，不看单纯增减
MARGIN_PCT_HIGH = 75.0
MARGIN_PCT_LOW = 40.0
MARGIN_DELTA_FLAT = 300.0  # 约 21 日变动小于此视为横盘（亿）

# 郭嘉队ETF：仓位分位优先，权重最大
NT_PCT_HIGH = 75.0
NT_PCT_LOW = 40.0
NT_DELTA_FLAT = 5.0  # 亿份

# 每项怎么打分（报告和网页共用）
SCORE_RULES: dict[str, str] = {
    "main_force": "周净流出 ≥2000 → −2；≥1000 → −1。只作风控，偶发净流入不当作看多。",
    "national_team": (
        "最高优先级。看宽基份额历史分位（仓位）× 月变动。"
        "高位减仓 −4；高位仍加/钝化 −2；低位加仓 +3；中位减 −2、加 +1。"
        "≠汇金/证金净交易，但是现有最能代表国家队仓位的序列。"
    ),
    "margin": (
        "不看单纯增减，看融资余额历史分位 × 近约21日方向。"
        "高位持续加 → −3（风险升，警惕离开）；高位去杠杆 → −2；"
        "低位加 → +2、低位减 → −1（低位变动宜跟随）；中位 ±1。"
    ),
    "divergence": "偏离 ≥16 → −1。从约 15 以上明显收窄 → −1（切向价值）。",
    "breadth": "近 5 日高低比 ≥8 → +1（偏热）；≤0.2 → −1（偏冷）。指数仍涨且极冷 → 背离 −1。",
    "unlock": "本月或下月解禁 ≥ 近 24 完整月中位数的 1.5 倍 → −1。解禁后两三个月主力流出容易偏高。",
}

# 三资金角色一句话
FUND_ROLES: dict[str, str] = {
    "national_team": "最高优先 · 仓位分位决定大问题",
    "margin": "分位×方向 · 高位加则离开，低位变动跟随",
    "main_force": "降温因子 · 市场越热，退出越多",
}


def _as_pct(raw: float | None) -> float | None:
    if raw is None or not pd.notna(raw):
        return None
    v = float(raw)
    if v <= 1.5:
        v *= 100.0
    return max(0.0, min(100.0, v))


def _zone(pct: float | None, high: float, low: float) -> str:
    if pct is None:
        return "gap"
    if pct >= high:
        return "high"
    if pct <= low:
        return "low"
    return "mid"


def score_margin_regime(pct: float | None, delta: float) -> tuple[int, str, str, str]:
    """返回 (score, zone, trend, tag)。trend: up/down/flat。"""
    zone = _zone(pct, MARGIN_PCT_HIGH, MARGIN_PCT_LOW)
    if abs(delta) < MARGIN_DELTA_FLAT:
        trend = "flat"
    elif delta > 0:
        trend = "up"
    else:
        trend = "down"
    if zone == "gap":
        return 0, zone, trend, "无分位"
    if zone == "high":
        if trend == "up":
            return -3, zone, trend, "高位还在加，风险在升，警惕离开"
        if trend == "down":
            return -2, zone, trend, "高位去杠杆，杀杠杆进行中"
        return -1, zone, trend, "高位钝化，仓位仍贵"
    if zone == "low":
        if trend == "up":
            return 2, zone, trend, "低位加杠杆，跟随"
        if trend == "down":
            return -1, zone, trend, "低位减杠杆，跟随偏空"
        return 1, zone, trend, "低位企稳，可跟随"
    if trend == "up":
        return 1, zone, trend, "中位加杠杆，温和跟随"
    if trend == "down":
        return -1, zone, trend, "中位减杠杆"
    return 0, zone, trend, "中位横盘"


def score_national_team_regime(pct: float | None, delta: float) -> tuple[int, str, str, str]:
    """郭嘉队ETF：仓位分位优先，分值幅度最大。trend: in/out/flat。"""
    zone = _zone(pct, NT_PCT_HIGH, NT_PCT_LOW)
    if abs(delta) < NT_DELTA_FLAT:
        trend = "flat"
    elif delta > 0:
        trend = "in"
    else:
        trend = "out"
    if zone == "gap":
        return 0, zone, trend, "无份额分位"
    if zone == "high":
        if trend == "out":
            return -4, zone, trend, "高仓位还在减——优先级最高，说明大问题"
        if trend == "in":
            return -2, zone, trend, "高仓位还在加，拥挤，警惕"
        return -2, zone, trend, "高仓位钝化，风险偏大"
    if zone == "low":
        if trend == "in":
            return 3, zone, trend, "低仓位加仓，托举，可跟随"
        if trend == "out":
            return -1, zone, trend, "低仓位仍减，力度有限"
        return 1, zone, trend, "低仓位企稳"
    if trend == "out":
        return -2, zone, trend, "中仓位减仓"
    if trend == "in":
        return 1, zone, trend, "中仓位加仓"
    return 0, zone, trend, "中仓位横盘"


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _prep(df: pd.DataFrame | None, cols: list[str]) -> pd.DataFrame:
    if df is None or getattr(df, "empty", True) or "date" not in getattr(df, "columns", []):
        return pd.DataFrame()
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    for col in cols:
        if col in out.columns:
            out[col] = _num(out[col])
    return out.dropna(subset=["date"]).sort_values("date")


def _item(
    item_id: str,
    label: str,
    *,
    score: int | None,
    note: str,
    status: str,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "label": label,
        "score": score,
        "note": note,
        "status": status,
        "rule": SCORE_RULES.get(item_id, ""),
    }


def _margin_item(margin: pd.DataFrame) -> dict[str, Any]:
    x = _prep(margin, ["融资余额_亿元", "分位_全样本"])
    if x.empty or "融资余额_亿元" not in x.columns:
        return _item("margin", "杠杆", score=None, note="无融资余额", status="gap")
    x = x.dropna(subset=["融资余额_亿元"])
    if len(x) < 25:
        return _item("margin", "杠杆", score=None, note="融资余额样本不足一月", status="gap")
    last = x.iloc[-1]
    prev = x.iloc[-21]
    delta = float(last["融资余额_亿元"] - prev["融资余额_亿元"])
    pct_v = _as_pct(float(last["分位_全样本"]) if pd.notna(last.get("分位_全样本")) else None)
    score, zone, trend, tag = score_margin_regime(pct_v, delta)
    pct_txt = f"，全样本分位 {pct_v:.0f}%（{zone}）" if pct_v is not None else "，无分位"
    note = (
        f"{pd.Timestamp(last['date']).date()} 融资 {float(last['融资余额_亿元']):.0f} 亿"
        f"{pct_txt}，约21日 {delta:+.0f} 亿（{trend}）。{tag}"
    )
    out = _item("margin", "杠杆", score=score, note=note, status="ok")
    out["zone"] = zone
    out["trend"] = trend
    out["direction"] = {"up": "in", "down": "out", "flat": "flat"}.get(trend, "flat")
    out["pct"] = pct_v
    return out


def _divergence_item(gv: pd.DataFrame) -> tuple[dict[str, Any], str]:
    """返回 (条目, 形态)。形态：extreme / narrowing / calm / gap。"""
    x = _prep(gv, ["偏离度"])
    if x.empty or "偏离度" not in x.columns:
        return _item("divergence", "偏离", score=None, note="无成长价值偏离", status="gap"), "gap"
    x = x.dropna(subset=["偏离度"])
    if x.empty:
        return _item("divergence", "偏离", score=None, note="偏离度为空", status="gap"), "gap"
    last = float(x["偏离度"].iloc[-1])
    last_day = pd.Timestamp(x["date"].iloc[-1]).date()
    earlier = x.loc[x["date"] <= x["date"].iloc[-1] - pd.Timedelta(days=60)]
    prev = float(earlier["偏离度"].iloc[-1]) if not earlier.empty else None
    narrowing = prev is not None and prev >= DIVERGENCE_EXTREME - 1 and last < prev - 0.4
    if last >= DIVERGENCE_EXTREME:
        shape = "extreme"
        score = -1
        tag = f"接近或达到对照值 {DIVERGENCE_EXTREME:.0f}"
    elif narrowing:
        shape = "narrowing"
        score = -1
        tag = "从高位收窄，资金可能从成长切向价值"
    else:
        shape = "calm"
        score = 0
        tag = "未到极值"
    prev_txt = f"，约60日前 {prev:.1f}" if prev is not None else ""
    note = f"{last_day} 偏离 {last:.1f}{prev_txt}。{tag}"
    return _item("divergence", "偏离", score=score, note=note, status="ok"), shape


def _breadth_item(hl: pd.DataFrame, weekly: pd.DataFrame) -> tuple[dict[str, Any], str]:
    x = _prep(hl, ["new_high", "new_low"])
    if x.empty or not {"new_high", "new_low"}.issubset(x.columns):
        return _item("breadth", "52周广度", score=None, note="无新高新低", status="gap"), "gap"
    x = x.dropna(subset=["new_high", "new_low"])
    if x.empty:
        return _item("breadth", "52周广度", score=None, note="新高新低为空", status="gap"), "gap"
    tail = x.tail(5)
    hi = float(tail["new_high"].mean())
    lo = float(tail["new_low"].mean())
    ratio = hi / max(lo, 1.0)
    w = _prep(weekly, ["close"])
    index_up = False
    if not w.empty and "close" in w.columns and len(w) >= 5:
        index_up = float(w["close"].iloc[-1]) > float(w["close"].iloc[-5])
    day = pd.Timestamp(x["date"].iloc[-1]).date()
    if ratio >= HL_HOT_RATIO:
        shape, score, tag = "hot", 1, "新高远多于新低，市场偏热"
    elif ratio <= HL_COLD_RATIO and index_up:
        shape, score, tag = "diverge", -1, "指数近几周仍升，但新低远多于新高"
    elif ratio <= HL_COLD_RATIO:
        shape, score, tag = "cold", -1, "新低远多于新高"
    else:
        shape, score, tag = "mixed", 0, "高低比不在极端"
    note = f"截至 {day} 近5日均新高 {hi:.0f}、新低 {lo:.0f}，高低比 {ratio:.2f}。{tag}"
    return _item("breadth", "52周广度", score=score, note=note, status="ok"), shape


def _unlock_item(unlock: pd.DataFrame) -> tuple[dict[str, Any], bool]:
    x = _prep(unlock, ["amount_yi"])
    if x.empty or "universe" not in x.columns:
        return _item("unlock", "解禁", score=None, note="无解禁金额", status="gap"), False
    all_df = x.loc[x["universe"].astype(str) == "all"].dropna(subset=["amount_yi"])
    if all_df.empty:
        return _item("unlock", "解禁", score=None, note="无全市场解禁", status="gap"), False
    all_df = all_df.set_index("date").sort_index()
    monthly = all_df["amount_yi"].resample("ME").sum(min_count=1).dropna()
    today = pd.Timestamp.today().normalize()
    history = monthly.loc[monthly.index < today.replace(day=1)]
    window = history.tail(24)
    base = float(window.median()) if len(window) else None
    this_end = today.to_period("M").end_time
    this_end = pd.Timestamp(this_end).tz_localize(None).normalize() if getattr(this_end, "tzinfo", None) else pd.Timestamp(this_end).normalize()
    nxt = (today + pd.offsets.MonthEnd(1)).normalize()
    if nxt <= this_end:
        nxt = (this_end + pd.offsets.MonthEnd(1)).normalize()
    this_val = float(monthly.loc[this_end]) if this_end in monthly.index else None
    next_val = float(monthly.loc[nxt]) if nxt in monthly.index else None
    hot_this = base and this_val is not None and this_val >= base * UNLOCK_HOT_MULT
    hot_next = base and next_val is not None and next_val >= base * UNLOCK_HOT_MULT
    hot = bool(hot_this or hot_next)
    base_txt = f"近24个完整月中位数 {base:.0f} 亿" if base else "历史月中位数不足"
    this_txt = f"本月 {this_val:.0f} 亿" if this_val is not None else "本月无汇总"
    next_txt = f"下月 {next_val:.0f} 亿" if next_val is not None else "下月无汇总"
    if hot:
        score, tag = -1, "高于月中位数约1.5倍，之后两三个月主力流出容易偏高"
    else:
        score, tag = 0, "未见明显解禁高峰"
    note = f"{this_txt}，{next_txt}；{base_txt}。{tag}"
    return _item("unlock", "解禁", score=score, note=note, status="ok"), hot


def _main_force_item(mf: pd.DataFrame) -> dict[str, Any]:
    x = _prep(mf, ["净额_亿元"])
    if x.empty or "净额_亿元" not in x.columns:
        return _item("main_force", "主力", score=None, note="无主力净额", status="gap")
    x = x.dropna(subset=["净额_亿元"])
    if len(x) < 20:
        return _item(
            "main_force",
            "主力",
            score=None,
            note=f"只有 {len(x)} 日，周流出还看不了",
            status="gap",
        )
    x = x.set_index("date").sort_index()
    weekly = x["净额_亿元"].resample("W-FRI").sum(min_count=1).dropna()
    if weekly.empty:
        return _item("main_force", "主力", score=None, note="无法聚周", status="gap")
    last = float(weekly.iloc[-1])
    day = pd.Timestamp(weekly.index[-1]).date()
    if last <= MAIN_FORCE_WEEK_EXTREME:
        score, tag = -2, "周净流出达到2000亿一档"
    elif last <= -1000:
        score, tag = -1, "周净流出高于约1000亿的周均量级"
    else:
        score, tag = 0, "未到周极端流出"
    note = f"{day} 当周合计 {last:.0f} 亿。{tag}"
    return _item("main_force", "主力", score=score, note=note, status="ok")


def _national_team_item(etf_monthly: pd.DataFrame | None) -> dict[str, Any]:
    """宽基份额仓位分位 × 月变动；最高优先级进分。≠汇金/证金身份。"""
    x = etf_monthly if isinstance(etf_monthly, pd.DataFrame) else pd.DataFrame()
    if x is None or x.empty or "份额_亿份" not in getattr(x, "columns", []):
        return _item(
            "national_team",
            "郭嘉队ETF",
            score=None,
            note="无宽基份额序列；正口径汇金+证金也没有",
            status="gap",
        )
    y = x.copy()
    if "date" in y.columns:
        y["date"] = pd.to_datetime(y["date"], errors="coerce")
        y = y.dropna(subset=["date"]).sort_values("date")
    y["份额_亿份"] = pd.to_numeric(y["份额_亿份"], errors="coerce")
    if "月变动_亿份" in y.columns:
        y["月变动_亿份"] = pd.to_numeric(y["月变动_亿份"], errors="coerce")
    y = y.dropna(subset=["份额_亿份"])
    if len(y) < 6:
        return _item(
            "national_team",
            "郭嘉队ETF",
            score=None,
            note=f"份额样本只有 {len(y)} 个月，仓位分位还看不稳",
            status="gap",
        )
    last = y.iloc[-1]
    day = pd.Timestamp(last["date"]).date() if "date" in last.index and pd.notna(last.get("date")) else None
    shares = float(last["份额_亿份"])
    if "月变动_亿份" in last.index and pd.notna(last.get("月变动_亿份")):
        delta = float(last["月变动_亿份"])
    elif len(y) >= 2:
        delta = shares - float(y["份额_亿份"].iloc[-2])
    else:
        delta = 0.0
    # 截至当月的历史分位（含当期）
    hist = y["份额_亿份"]
    pct_v = float((hist <= shares).mean() * 100.0)
    score, zone, trend, tag = score_national_team_regime(pct_v, delta)
    day_txt = f"{day} " if day else ""
    note = (
        f"{day_txt}份额 {shares:.0f} 亿份，历史分位 {pct_v:.0f}%（{zone}），"
        f"月变动 {delta:+.1f} 亿份（{trend}）。{tag}。≠汇金/证金净交易。"
    )
    out = _item("national_team", "郭嘉队ETF", score=score, note=note, status="ok")
    out["zone"] = zone
    out["trend"] = trend
    out["direction"] = trend if trend in {"in", "out", "flat"} else "flat"
    out["pct"] = pct_v
    out["proxy_delta"] = delta
    return out


def _flow_direction(item: dict[str, Any]) -> str | None:
    """资金方向 in/out/flat；与得分正负脱钩（高位加杠杆得分可为负）。"""
    if item.get("status") == "gap":
        return None
    if item.get("direction") in {"in", "out", "flat"}:
        return str(item["direction"])
    sc = item.get("score")
    if sc is None:
        return None
    if int(sc) > 0:
        return "in"
    if int(sc) < 0:
        return "out"
    return "flat"


def _resonance(fund_items: list[dict[str, Any]]) -> dict[str, Any]:
    """框架：一起流入强涨 / 一起撤出强跌 / 不一致震荡。"""
    dirs = [(it["id"], _flow_direction(it)) for it in fund_items]
    known = [(i, d) for i, d in dirs if d in {"in", "out"}]
    bits = []
    for it in fund_items:
        d = _flow_direction(it)
        lab = it.get("label") or it.get("id")
        if it.get("status") == "gap":
            bits.append(f"{lab}缺")
        elif d == "in":
            bits.append(f"{lab}流入")
        elif d == "out":
            bits.append(f"{lab}撤出")
        else:
            bits.append(f"{lab}平")
    detail = " · ".join(bits)
    if len(known) < 2:
        return {
            "tone": "gap",
            "text": f"三资金证据不齐，共振先不谈（{detail}）",
            "dirs": {i: d for i, d in dirs},
        }
    flows = {d for _, d in known}
    if flows == {"in"}:
        return {"tone": "in", "text": f"可见资金偏同向流入 · 偏强（{detail}）", "dirs": {i: d for i, d in dirs}}
    if flows == {"out"}:
        return {"tone": "out", "text": f"可见资金偏同向撤出 · 偏弱（{detail}）", "dirs": {i: d for i, d in dirs}}
    return {"tone": "mixed", "text": f"可见资金方向不一致 · 震荡（{detail}）", "dirs": {i: d for i, d in dirs}}


def _triple_card(item: dict[str, Any]) -> dict[str, Any]:
    sc = item.get("score")
    if sc is None:
        score_text = "缺"
    else:
        score_text = f"+{int(sc)}" if int(sc) > 0 else str(int(sc))
    zone = item.get("zone")
    zone_cn = {"high": "高位", "mid": "中位", "low": "低位"}.get(str(zone or ""), "")
    role = FUND_ROLES.get(item["id"], "")
    if zone_cn:
        role = f"{role} · 现{zone_cn}" if role else f"现{zone_cn}"
    return {
        "id": item["id"],
        "label": item["label"],
        "score": sc,
        "score_text": score_text,
        "status": item.get("status") or "gap",
        "direction": _flow_direction(item),
        "zone": zone,
        "role": role,
        "note": item.get("note") or "",
        "rule": item.get("rule") or "",
    }


def _funds_month_table(bundle: dict[str, Any], *, months: int = 8) -> list[dict[str, Any]]:
    """片子里那种月对照：主力月净额 / 郭嘉队ETF份额变动 / 融资月变动。"""
    margin = _prep(bundle.get("margin"), ["融资余额_亿元"])
    mf = _prep(bundle.get("main_force_daily"), ["净额_亿元"])
    etf_m = bundle.get("etf_monthly") if isinstance(bundle.get("etf_monthly"), pd.DataFrame) else pd.DataFrame()

    m_delta = pd.Series(dtype=float)
    if not margin.empty and "融资余额_亿元" in margin.columns:
        lvl = margin.dropna(subset=["融资余额_亿元"]).set_index("date").sort_index()["融资余额_亿元"]
        m_end = lvl.resample("ME").last().dropna()
        m_delta = m_end.diff()

    mf_m = pd.Series(dtype=float)
    if not mf.empty and "净额_亿元" in mf.columns and len(mf) >= 5:
        mf_m = mf.set_index("date").sort_index()["净额_亿元"].resample("ME").sum(min_count=1).dropna()

    etf_s = pd.Series(dtype=float)
    if isinstance(etf_m, pd.DataFrame) and not etf_m.empty and "月变动_亿份" in etf_m.columns:
        e = etf_m.copy()
        e["date"] = pd.to_datetime(e["date"], errors="coerce")
        e = e.dropna(subset=["date"]).sort_values("date")
        etf_s = pd.Series(
            pd.to_numeric(e["月变动_亿份"], errors="coerce").values,
            index=pd.DatetimeIndex(e["date"]).normalize(),
        ).dropna()

    periods = sorted(
        {
            pd.Timestamp(t).to_period("M")
            for s in (m_delta, mf_m, etf_s)
            if isinstance(s.index, pd.DatetimeIndex)
            for t in s.index
        }
    )
    if not periods:
        return []
    periods = periods[-months:]

    def _v(s: pd.Series, per: pd.Period) -> float | None:
        if s is None or s.empty or not isinstance(s.index, pd.DatetimeIndex):
            return None
        same = s.loc[s.index.to_period("M") == per]
        if len(same):
            return float(same.iloc[-1])
        return None

    rows: list[dict[str, Any]] = []
    for per in periods:
        rows.append(
            {
                "month": str(per),
                "main_force": _v(mf_m, per),
                "national_team": _v(etf_s, per),
                "margin": _v(m_delta, per),
            }
        )
    return rows


def score_history_weekly(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """按周复算可见项合计分，供主图下方打分曲线（轴 −10～10）。缺项不记 0。"""
    weekly = _prep(bundle.get("weekly"), ["close"])
    margin = _prep(bundle.get("margin"), ["融资余额_亿元", "分位_全样本"])
    gv = _prep(bundle.get("growth_value"), ["偏离度"])
    hl = _prep(bundle.get("high_low"), ["new_high", "new_low"])
    unlock = _prep(bundle.get("unlock_daily"), ["amount_yi"])
    mf = _prep(bundle.get("main_force_daily"), ["净额_亿元"])
    etf_m = bundle.get("etf_monthly") if isinstance(bundle.get("etf_monthly"), pd.DataFrame) else pd.DataFrame()

    if not weekly.empty:
        anchors = list(weekly["date"])
    elif not margin.empty:
        anchors = list(margin.set_index("date").resample("W-FRI").last().dropna(how="all").index)
    else:
        return []

    m_lvl = (
        margin.dropna(subset=["融资余额_亿元"]).set_index("date").sort_index()["融资余额_亿元"]
        if not margin.empty
        else pd.Series(dtype=float)
    )
    m_pct = (
        margin.dropna(subset=["分位_全样本"]).set_index("date").sort_index()["分位_全样本"]
        if not margin.empty and "分位_全样本" in margin.columns
        else pd.Series(dtype=float)
    )
    div = (
        gv.dropna(subset=["偏离度"]).set_index("date").sort_index()["偏离度"]
        if not gv.empty
        else pd.Series(dtype=float)
    )
    hi = (
        hl.dropna(subset=["new_high", "new_low"]).set_index("date").sort_index()
        if not hl.empty
        else pd.DataFrame()
    )
    unlock_all = pd.Series(dtype=float)
    if not unlock.empty and "universe" in unlock.columns:
        ua = unlock.loc[unlock["universe"].astype(str) == "all"].dropna(subset=["amount_yi"])
        if not ua.empty:
            unlock_all = ua.set_index("date").sort_index()["amount_yi"].resample("ME").sum(min_count=1).dropna()
    mf_w = pd.Series(dtype=float)
    if not mf.empty and len(mf) >= 20:
        mf_w = mf.set_index("date").sort_index()["净额_亿元"].resample("W-FRI").sum(min_count=1).dropna()

    etf_shares = pd.Series(dtype=float)
    etf_delta = pd.Series(dtype=float)
    if isinstance(etf_m, pd.DataFrame) and not etf_m.empty and "份额_亿份" in etf_m.columns:
        e = etf_m.copy()
        e["date"] = pd.to_datetime(e["date"], errors="coerce")
        e = e.dropna(subset=["date"]).sort_values("date")
        e["份额_亿份"] = pd.to_numeric(e["份额_亿份"], errors="coerce")
        e = e.dropna(subset=["份额_亿份"])
        if not e.empty:
            etf_shares = pd.Series(e["份额_亿份"].values, index=pd.DatetimeIndex(e["date"]).normalize())
            if "月变动_亿份" in e.columns:
                etf_delta = pd.Series(
                    pd.to_numeric(e["月变动_亿份"], errors="coerce").values,
                    index=etf_shares.index,
                )

    out: list[dict[str, Any]] = []
    for day in anchors:
        end = pd.Timestamp(day).normalize()
        total = 0
        scored = 0

        # 郭嘉队ETF：最高优先，先加
        if len(etf_shares.loc[:end]) >= 6:
            hist = etf_shares.loc[:end]
            shares = float(hist.iloc[-1])
            pct_nt = float((hist <= shares).mean() * 100.0)
            if len(etf_delta.loc[:end]) and pd.notna(etf_delta.loc[:end].iloc[-1]):
                d_nt = float(etf_delta.loc[:end].iloc[-1])
            elif len(hist) >= 2:
                d_nt = shares - float(hist.iloc[-2])
            else:
                d_nt = 0.0
            sc_nt, _, _, _ = score_national_team_regime(pct_nt, d_nt)
            total += sc_nt
            scored += 1

        if len(m_lvl.loc[:end]) >= 22:
            tail = m_lvl.loc[:end]
            delta = float(tail.iloc[-1] - tail.iloc[-22])
            pct_raw = float(m_pct.loc[:end].iloc[-1]) if len(m_pct.loc[:end]) else None
            pct_v = _as_pct(pct_raw)
            sc_m, _, _, _ = score_margin_regime(pct_v, delta)
            total += sc_m
            scored += 1

        if len(div.loc[:end]):
            last = float(div.loc[:end].iloc[-1])
            earlier = div.loc[: end - pd.Timedelta(days=60)]
            prev = float(earlier.iloc[-1]) if len(earlier) else None
            if last >= DIVERGENCE_EXTREME:
                total -= 1
            elif prev is not None and prev >= DIVERGENCE_EXTREME - 1 and last < prev - 0.4:
                total -= 1
            scored += 1

        if not hi.empty:
            htail = hi.loc[:end].tail(5)
            if len(htail):
                ratio = float(htail["new_high"].mean()) / max(float(htail["new_low"].mean()), 1.0)
                if ratio >= HL_HOT_RATIO:
                    total += 1
                elif ratio <= HL_COLD_RATIO:
                    total -= 1
                scored += 1

        if len(unlock_all):
            this_end = end.to_period("M").to_timestamp("M")
            history = unlock_all.loc[unlock_all.index < end.replace(day=1)]
            base = float(history.tail(24).median()) if len(history) else None
            this_val = float(unlock_all.loc[this_end]) if this_end in unlock_all.index else None
            nxt = (this_end + pd.offsets.MonthEnd(1)).normalize()
            next_val = float(unlock_all.loc[nxt]) if nxt in unlock_all.index else None
            hot = bool(
                base
                and (
                    (this_val is not None and this_val >= base * UNLOCK_HOT_MULT)
                    or (next_val is not None and next_val >= base * UNLOCK_HOT_MULT)
                )
            )
            if hot:
                total -= 1
            scored += 1

        if len(mf_w.loc[:end]):
            last_mf = float(mf_w.loc[:end].iloc[-1])
            if last_mf <= MAIN_FORCE_WEEK_EXTREME:
                total -= 2
            elif last_mf <= -1000:
                total -= 1
            scored += 1

        if scored == 0:
            continue
        total = max(-10, min(10, int(total)))
        out.append({"time": end.strftime("%Y-%m-%d"), "value": total})
    return out


def diagnose_position(bundle: dict[str, Any]) -> dict[str, Any]:
    """根据已入库序列给一个位置判断。结构对齐框架.md：三资金 → 行为 → 提前量 → 仓位带。"""
    margin_item = _margin_item(bundle.get("margin"))
    div_item, div_shape = _divergence_item(bundle.get("growth_value"))
    br_item, br_shape = _breadth_item(bundle.get("high_low"), bundle.get("weekly"))
    unlock_item, unlock_hot = _unlock_item(bundle.get("unlock_daily"))
    mf_item = _main_force_item(bundle.get("main_force_daily"))
    nt_item = _national_team_item(bundle.get("etf_monthly"))
    # 展示顺序：郭嘉队ETF最高优先 → 杠杆 → 主力 → 行为 → 解禁
    items = [nt_item, margin_item, mf_item, div_item, br_item, unlock_item]
    gaps = [
        "主力长历史（东财窗口短）",
        "汇金+证金月变动（现用郭嘉队ETF份额仓位分位代理）",
        "公募新发 / 大股东减持 / 行业资金切换",
        "成交拥挤度、隐含波动、全球杠杆等扩展位",
    ]

    scored = [it for it in items if it["status"] == "ok" and it["score"] is not None]
    score = int(sum(int(it["score"]) for it in scored))
    leverage = int(margin_item["score"]) if margin_item["status"] == "ok" and margin_item["score"] is not None else None
    m_zone = str(margin_item.get("zone") or "gap")
    m_trend = str(margin_item.get("trend") or "flat")
    nt_zone = str(nt_item.get("zone") or "gap")
    nt_trend = str(nt_item.get("trend") or "flat")
    behavior_bad = div_shape == "extreme" or br_shape in {"cold", "diverge"}
    funds_worse = (leverage is not None and leverage < 0) or unlock_hot or (
        mf_item["status"] == "ok" and int(mf_item["score"] or 0) < 0
    ) or (nt_item["status"] == "ok" and int(nt_item["score"] or 0) <= -2)

    # 位置语言：郭嘉队ETF仓位优先，其次融资分位×方向
    if nt_item["status"] == "ok" and nt_zone == "high" and nt_trend == "out":
        state = "国家队高位退出"
        summary = "郭嘉队ETF 高仓位还在减。这是最高优先级信号，说明大问题。"
    elif nt_item["status"] == "ok" and nt_zone == "high" and nt_trend == "in":
        state = "国家队高位拥挤"
        summary = "郭嘉队ETF 高仓位还在加。拥挤，警惕，不宜追。"
    elif m_zone == "high" and m_trend == "up":
        state = "融资高位加杠杆，警惕离开"
        summary = "融资余额历史分位已高，还在增加。风险在升，需要警惕离开。"
    elif m_zone == "high" and m_trend == "down":
        state = "杀杠杆进行中"
        summary = "融资高位去杠杆。余额没止住，只留底仓。"
    elif m_zone == "low" and m_trend in {"up", "down", "flat"}:
        state = "融资低位，跟随"
        summary = (
            "融资余额在历史低位附近。低位的增减更宜跟随，而不是按高位逻辑反着做。"
            if m_trend != "flat"
            else "融资余额在历史低位附近企稳，可跟随。"
        )
    elif behavior_bad and funds_worse:
        state = "红灯：行为已极值，资金侧在变差"
        summary = "偏离或新高新低已经极端，郭嘉队/杠杆/解禁/主力里又有一项在变差。"
    elif m_zone == "high" and m_trend == "up" and div_shape == "extreme":
        state = "杠杆肥尾"
        summary = "融资高位还在加，偏离也高。涨可以继续，跌起来也快。"
    elif div_shape == "narrowing":
        state = "偏离收窄"
        summary = "成长相对价值的偏离从高位往下。少成长，多价值。"
    elif br_shape == "hot" and m_zone == "high":
        state = "过热"
        summary = "新高远多于新低，融资仓位又在高位。这种时候不追。"
    else:
        state = "震荡 / 证据不够"
        summary = "三资金方向凑不齐，或最高优先项（郭嘉队ETF）证据还不够硬。"

    from data_source.dp_cycle.allocation import (
        DECISIONS,
        PHASES,
        allocation_view,
        choose_phase,
        decide_retail_action,
        monthly_regimes,
    )

    mf_score = int(mf_item["score"]) if mf_item["status"] == "ok" and mf_item["score"] is not None else None
    nt_score = int(nt_item["score"]) if nt_item["status"] == "ok" and nt_item["score"] is not None else None
    phase_id = choose_phase(
        margin_zone=m_zone,
        margin_trend=m_trend,
        nt_zone=nt_zone,
        nt_trend=nt_trend,
        div_shape=div_shape,
        br_shape=br_shape,
        unlock_hot=unlock_hot,
        mf_score=mf_score,
        nt_score=nt_score,
    )
    partial = mf_item["status"] != "ok"
    position = allocation_view(phase_id, partial=partial)
    decision = decide_retail_action(
        score=score,
        nt_zone=nt_zone,
        nt_trend=nt_trend,
        margin_zone=m_zone,
        margin_trend=m_trend,
        phase_id=phase_id,
        behavior_bad=behavior_bad,
    )
    # 仓位带以决策为准（散户动作优先于阶段名）
    position["equity_low"] = decision["equity_low"]
    position["equity_high"] = decision["equity_high"]
    position["equity_text"] = decision["equity_text"]
    position["decision"] = decision["id"]
    position["decision_label"] = decision["label"]
    phases = []
    for row in PHASES:
        if row["id"] == "unclear":
            continue
        phases.append(
            {
                "id": row["id"],
                "actor": row["actor"],
                "side": row["side"],
                "stage": row["stage"],
                "equity_text": (
                    f"{row['equity_low']}–{row['equity_high']}%"
                    if row["equity_low"] != row["equity_high"]
                    else f"{row['equity_low']}%"
                ),
                "active": row["id"] == phase_id,
            }
        )
    periods = [row for row in monthly_regimes(bundle) if row.get("phase_id") != "unclear"]

    def _node(nid: str, title: str, status: str, text: str) -> dict[str, Any]:
        return {"id": nid, "title": title, "status": status, "text": text}

    # 三种系统性风险：文案跟分位×方向走
    if m_zone == "high" and m_trend == "up":
        n_lev = _node("leverage", "杀杠杆/高位风险", "on", "融资高位还在加。不是杀杠杆，是风险在升，警惕离开。")
    elif m_zone == "high" and m_trend == "down":
        n_lev = _node("leverage", "杀杠杆/高位风险", "on", "融资高位去杠杆。跌势看余额是否止住。")
    elif m_zone == "high":
        n_lev = _node("leverage", "杀杠杆/高位风险", "watch", "融资仓位仍在高位，方向横盘。")
    elif m_zone == "low":
        n_lev = _node("leverage", "杀杠杆/高位风险", "off", "融资在低位。低位变动宜跟随，不是高位离开逻辑。")
    elif leverage is not None:
        n_lev = _node("leverage", "杀杠杆/高位风险", "off", "融资在中位，按分位×方向温和解读。")
    else:
        n_lev = _node("leverage", "杀杠杆/高位风险", "gap", "无融资序列。")

    if mf_item["status"] == "ok" and int(mf_item["score"] or 0) <= -2:
        n_exit = _node("exit", "股东退出", "on", "主力周流出到 2000 亿一档。")
    elif unlock_hot:
        n_exit = _node("exit", "股东退出", "watch", "解禁高峰已亮。之后两三个月主力流出容易偏高。")
    elif mf_item["status"] != "ok":
        n_exit = _node("exit", "股东退出", "gap", "主力长历史不够，这项先空着。")
    else:
        n_exit = _node("exit", "股东退出", "off", "主力周流出未到极端。")

    if nt_item["status"] == "gap":
        n_nt = _node("national_team", "国家队退出/过热调控", "gap", "郭嘉队ETF份额序列不足，先空着。")
    elif nt_zone == "high" and nt_trend == "out":
        n_nt = _node("national_team", "国家队退出/过热调控", "on", "高仓位还在减——最高优先，说明大问题。")
    elif nt_zone == "high":
        n_nt = _node("national_team", "国家队退出/过热调控", "watch", "高仓位拥挤或钝化，警惕。")
    elif nt_zone == "low" and nt_trend == "in":
        n_nt = _node("national_team", "国家队退出/过热调控", "off", "低仓位加仓，托举信号，可跟随。")
    else:
        n_nt = _node(
            "national_team",
            "国家队退出/过热调控",
            "off" if int(nt_item.get("score") or 0) >= 0 else "watch",
            nt_item.get("note") or "郭嘉队ETF仓位中性。",
        )

    nodes = [n_nt, n_lev, n_exit]
    fund_items = [nt_item, margin_item, mf_item]
    triple = [_triple_card(it) for it in fund_items]
    resonance = _resonance(fund_items)
    layers = [
        {
            "id": "funds",
            "title": "A. 资金三件套",
            "hint": "郭嘉队ETF仓位优先（±3/±4）· 融资分位×方向 · 主力风控",
            "items": fund_items,
        },
        {
            "id": "behavior",
            "title": "B. 行为两件套",
            "hint": "极值只说明分裂；下跌仍看资金侧边际",
            "items": [div_item, br_item],
        },
        {
            "id": "ahead",
            "title": "C. 提前量",
            "hint": "解禁后两三个月主力流出容易偏高",
            "items": [unlock_item],
        },
    ]
    score_logic = (
        "你是散户：盯其他几路资金重大改变。"
        "权重 郭嘉队ETF仓位 > 融资分位×方向 > 主力/行为/解禁。"
        "打分落到五档：离场 / 减仓 / 持有观望 / 加仓 / 抄底。缺的不记 0。"
    )

    return {
        "state": state,
        "summary": summary,
        "score": score,
        "score_note": f"已打分 {len(scored)} 项，合计 {score:+d}。缺的不记 0。",
        "score_logic": score_logic,
        "framework": "散户决策 · 盯其他几路资金 · 打分出仓位",
        "decision": decision,
        "decision_scale": [
            {
                "id": r["id"],
                "label": r["label"],
                "equity_text": f"{r['equity_low']}–{r['equity_high']}%",
                "active": r["id"] == decision["id"],
            }
            for r in DECISIONS
        ],
        "items": items,
        "layers": layers,
        "triple": triple,
        "resonance": resonance,
        "funds_month": _funds_month_table(bundle),
        "nodes": nodes,
        "gaps": gaps,
        "position": position,
        "phases": phases,
        "periods": periods,
        "score_curve": score_history_weekly(bundle),
    }
