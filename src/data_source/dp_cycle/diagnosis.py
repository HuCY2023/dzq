# -*- coding: utf-8 -*-
"""大盘位置诊断。

规则来自 research/DP/大周期/框架.md。
只给已经有序列的项打分。缺数据的项列入缺口，不记成 0 分。
作者 2026-06 第四周的 -9 还包含公募新发、减持、行业资金等，这里复刻不了。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from data_source.dp_cycle.rules import (  # noqa: E402
    DIVERGENCE_EXTREME,
    HL_COLD_RATIO,
    HL_HOT_RATIO,
    MAIN_FORCE_WEEK_EXTREME,
    NT_PCT_HIGH,
    NT_PCT_LOW,
    NT_SHORT_WINDOW,
    UNLOCK_HOT_MULT,
    breadth_shape,
    divergence_shape,
    score_breadth,
    score_divergence,
    score_fund_issue,
    score_holder_reduce,
    score_industry_flow,
    score_margin_regime,
    score_national_team_regime,
    score_scissors,
    score_unlock_hot,
    decide_retail_action_core,
)

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
    "scissors": "周「主力−(中单+小单)」≤ −3000 亿 → −1。中单小单单独不作看多。",
    "fund_issue": "公募新发：上周 ≥300 亿份且本周不到一半 → −1（片子 −9 周案例）。",
    "holder_reduce": "大股东减持：本周 ≥50 亿且比上周增 75% → −1。",
    "industry_flow": "行业资金：科技周净额 ≤ −80 且银行 ≥ −10 → −1。",
    "ashare_crowding": "成交拥挤度：例 62.8% 成交集中在前 10% 个股（扩展位，有湖再进分）。",
    "iv_spread": "科创隐含波动率分位远高于标普 VIX + 高杠杆 = 脆弱（扩展位）。",
    "us_crowding": "美股保证金/M2≈6% 警戒；VIX/信用利差旁证。不进 A 股合计分，只作全球脆弱提示。",
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


# score_margin_regime / score_national_team_regime 已移至 rules.py


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
    """宽基份额仓位分位 × 月变动；最高优先级进分。≠汇金/证金身份。
    双轨分位：全样本 + 近 NT_SHORT_WINDOW 月，取更极端一侧判 zone。"""
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
    # 双轨分位：全样本 + 近 N 月
    hist = y["份额_亿份"]
    pct_all = float((hist <= shares).mean() * 100.0)
    short_hist = hist.tail(NT_SHORT_WINDOW)
    pct_short = float((short_hist <= shares).mean() * 100.0) if len(short_hist) >= 12 else pct_all
    # 取更极端一侧（高仓位取大值，低仓位取小值）
    pct_v = max(pct_all, pct_short) if pct_all >= 50 else min(pct_all, pct_short)
    score, zone, trend, tag = score_national_team_regime(pct_v, delta)
    day_txt = f"{day} " if day else ""
    dual_txt = f"全{pct_all:.0f}%/近{NT_SHORT_WINDOW}月{pct_short:.0f}%"
    note = (
        f"{day_txt}份额 {shares:.0f} 亿份，分位 {dual_txt}→{pct_v:.0f}%（{zone}），"
        f"月变动 {delta:+.1f} 亿份（{trend}）。{tag}。≠汇金/证金净交易。"
    )
    out = _item("national_team", "郭嘉队ETF", score=score, note=note, status="ok")
    out["zone"] = zone
    out["trend"] = trend
    out["direction"] = trend if trend in {"in", "out", "flat"} else "flat"
    out["pct"] = pct_v
    out["pct_all"] = pct_all
    out["pct_short"] = pct_short
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
    """按周复算可见项合计分，供主图下方打分曲线（轴 −10～10）。缺项不记 0。

    性能优化：预转换 numpy 数组 + searchsorted 索引查找，避免每次循环创建 Series。
    """
    import numpy as np

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

    # ── 预转换 numpy 数组 + 日期索引，避免循环内重复 .loc[:end] ──
    def _to_np(series: pd.Series) -> tuple[np.ndarray, np.ndarray]:
        """返回 (dates_as_i8, values)。"""
        if series.empty:
            return np.array([], dtype="datetime64[ns]"), np.array([], dtype=float)
        idx = pd.DatetimeIndex(series.index).asi8
        return idx, series.to_numpy(dtype=float, na_value=np.nan)

    # 融资
    if not margin.empty:
        _m = margin.dropna(subset=["融资余额_亿元"]).set_index("date").sort_index()
        m_lvl_i8, m_lvl_v = _to_np(_m["融资余额_亿元"])
    else:
        m_lvl_i8, m_lvl_v = np.array([], dtype="int64"), np.array([], dtype=float)

    if not margin.empty and "分位_全样本" in margin.columns:
        _mp = margin.dropna(subset=["分位_全样本"]).set_index("date").sort_index()
        m_pct_i8, m_pct_v = _to_np(_mp["分位_全样本"])
    else:
        m_pct_i8, m_pct_v = np.array([], dtype="int64"), np.array([], dtype=float)

    # 偏离度
    if not gv.empty:
        _d = gv.dropna(subset=["偏离度"]).set_index("date").sort_index()
        div_i8, div_v = _to_np(_d["偏离度"])
    else:
        div_i8, div_v = np.array([], dtype="int64"), np.array([], dtype=float)

    # 广度
    if not hl.empty:
        _h = hl.dropna(subset=["new_high", "new_low"]).set_index("date").sort_index()
        hi_i8 = pd.DatetimeIndex(_h.index).asi8
        hi_high = _h["new_high"].to_numpy(dtype=float)
        hi_low = _h["new_low"].to_numpy(dtype=float)
    else:
        hi_i8 = np.array([], dtype="int64")
        hi_high = hi_low = np.array([], dtype=float)

    # 解禁（月度）
    unlock_i8 = np.array([], dtype="int64")
    unlock_v = np.array([], dtype=float)
    if not unlock.empty and "universe" in unlock.columns:
        ua = unlock.loc[unlock["universe"].astype(str) == "all"].dropna(subset=["amount_yi"])
        if not ua.empty:
            _u = ua.set_index("date").sort_index()["amount_yi"].resample("ME").sum(min_count=1).dropna()
            unlock_i8, unlock_v = _to_np(_u)

    # 主力周合计
    mf_w_v = np.array([], dtype=float)
    mf_w_i8 = np.array([], dtype="int64")
    gap_w_v = np.array([], dtype=float)
    gap_w_i8 = np.array([], dtype="int64")
    if not mf.empty and len(mf) >= 20:
        _mw = mf.set_index("date").sort_index()["净额_亿元"].resample("W-FRI").sum(min_count=1).dropna()
        mf_w_i8, mf_w_v = _to_np(_mw)
        if {"中单_亿元", "小单_亿元"} <= set(mf.columns):
            g = mf.dropna(subset=["净额_亿元", "中单_亿元", "小单_亿元"]).copy()
            if len(g) >= 20:
                g["_gap"] = g["净额_亿元"] - (g["中单_亿元"] + g["小单_亿元"])
                _gw = g.set_index("date").sort_index()["_gap"].resample("W-FRI").sum(min_count=1).dropna()
                gap_w_i8, gap_w_v = _to_np(_gw)

    # 公募新发 / 减持 / 行业资金
    def _ws_np(df: pd.DataFrame | None, value: str) -> tuple[np.ndarray, np.ndarray]:
        s = _weekly_sum(df, value)
        if s.empty:
            return np.array([], dtype="int64"), np.array([], dtype=float)
        return _to_np(s)

    issue_w_i8, issue_w_v = _ws_np(bundle.get("fund_issue"), "shares_yi")
    reduce_w_i8, reduce_w_v = _ws_np(bundle.get("holder_reduce"), "amount_yi")
    ind = _prep(bundle.get("industry_flow_daily"), ["net_yi"])
    tech_w_i8, tech_w_v = _ws_np(
        ind.loc[ind["name"].isin(_TECH_BOARDS)] if not ind.empty and "name" in ind.columns else None, "net_yi"
    )
    bank_w_i8, bank_w_v = _ws_np(
        ind.loc[ind["name"] == "银行"] if not ind.empty and "name" in ind.columns else None, "net_yi"
    )

    # ETF 份额
    etf_i8 = np.array([], dtype="int64")
    etf_shares_v = np.array([], dtype=float)
    etf_delta_v = np.array([], dtype=float)
    if isinstance(etf_m, pd.DataFrame) and not etf_m.empty and "份额_亿份" in etf_m.columns:
        e = etf_m.copy()
        e["date"] = pd.to_datetime(e["date"], errors="coerce")
        e = e.dropna(subset=["date"]).sort_values("date")
        e["份额_亿份"] = pd.to_numeric(e["份额_亿份"], errors="coerce")
        e = e.dropna(subset=["份额_亿份"])
        if not e.empty:
            etf_i8 = pd.DatetimeIndex(e["date"]).normalize().asi8
            etf_shares_v = e["份额_亿份"].to_numpy(dtype=float)
            if "月变动_亿份" in e.columns:
                etf_delta_v = pd.to_numeric(e["月变动_亿份"], errors="coerce").to_numpy(dtype=float)

    # ── 主循环：numpy 切片 + searchsorted ──
    out: list[dict[str, Any]] = []
    # 预转 anchor 时间戳 i8
    anchor_dt = pd.DatetimeIndex(anchors).normalize()
    anchor_i8 = anchor_dt.asi8

    for i, day in enumerate(anchors):
        end_i8 = anchor_i8[i]
        end_ts = anchor_dt[i]
        total = 0
        scored = 0

        # 郭嘉队ETF：最高优先，先加（双轨分位）
        if len(etf_i8) >= 6:
            ei = int(np.searchsorted(etf_i8, end_i8, side="right"))
            if ei >= 6:
                hist = etf_shares_v[:ei]
                shares = float(hist[-1])
                pct_all = float((hist <= shares).mean() * 100.0)
                short_n = min(NT_SHORT_WINDOW, len(hist))
                short_hist = hist[-short_n:]
                pct_short = float((short_hist <= shares).mean() * 100.0) if short_n >= 12 else pct_all
                pct_nt = max(pct_all, pct_short) if pct_all >= 50 else min(pct_all, pct_short)
                if len(etf_delta_v) and ei > 0 and not np.isnan(etf_delta_v[ei - 1]):
                    d_nt = float(etf_delta_v[ei - 1])
                elif ei >= 2:
                    d_nt = shares - float(hist[-2])
                else:
                    d_nt = 0.0
                sc_nt, _, _, _ = score_national_team_regime(pct_nt, d_nt)
                total += sc_nt
                scored += 1

        # 融资
        mi = int(np.searchsorted(m_lvl_i8, end_i8, side="right"))
        if mi >= 22:
            delta = float(m_lvl_v[mi - 1] - m_lvl_v[mi - 22])
            mpi = int(np.searchsorted(m_pct_i8, end_i8, side="right"))
            pct_raw = float(m_pct_v[mpi - 1]) if mpi > 0 else None
            pct_v = _as_pct(pct_raw)
            sc_m, _, _, _ = score_margin_regime(pct_v, delta)
            total += sc_m
            scored += 1

        # 偏离度
        di = int(np.searchsorted(div_i8, end_i8, side="right"))
        if di > 0:
            last = float(div_v[di - 1])
            cutoff_i8 = end_i8 - np.timedelta64(60, "D")
            ei60 = int(np.searchsorted(div_i8, cutoff_i8, side="right"))
            prev = float(div_v[ei60 - 1]) if ei60 > 0 else None
            total += score_divergence(divergence_shape(last, prev))
            scored += 1

        # 广度
        if len(hi_i8):
            hi_idx = int(np.searchsorted(hi_i8, end_i8, side="right"))
            if hi_idx > 0:
                start_b = max(0, hi_idx - 5)
                h_mean = float(hi_high[start_b:hi_idx].mean())
                l_mean = float(hi_low[start_b:hi_idx].mean())
                ratio = h_mean / max(l_mean, 1.0)
                total += score_breadth(breadth_shape(ratio, False))
                scored += 1

        # 解禁
        if len(unlock_i8):
            this_end = end_ts.to_period("M").to_timestamp("M")
            month1_i8 = np.datetime64(end_ts.replace(day=1).normalize(), "ns").view("int64")
            hist_idx = int(np.searchsorted(unlock_i8, month1_i8, side="left"))
            base = float(np.median(unlock_v[max(0, hist_idx - 24):hist_idx])) if hist_idx > 0 else None
            this_i8 = np.datetime64(this_end, "ns").view("int64")
            this_pos = int(np.searchsorted(unlock_i8, this_i8, side="left"))
            this_val = float(unlock_v[this_pos]) if this_pos < len(unlock_i8) and unlock_i8[this_pos] == this_i8 else None
            nxt = (this_end + pd.offsets.MonthEnd(1)).normalize()
            nxt_i8 = np.datetime64(nxt, "ns").view("int64")
            nxt_pos = int(np.searchsorted(unlock_i8, nxt_i8, side="left"))
            next_val = float(unlock_v[nxt_pos]) if nxt_pos < len(unlock_i8) and unlock_i8[nxt_pos] == nxt_i8 else None
            if score_unlock_hot(this_val, next_val, base):
                total -= 1
            scored += 1

        # 主力周合计
        mfi = int(np.searchsorted(mf_w_i8, end_i8, side="right"))
        if mfi > 0:
            last_mf = float(mf_w_v[mfi - 1])
            if last_mf <= MAIN_FORCE_WEEK_EXTREME:
                total -= 2
            elif last_mf <= -1000:
                total -= 1
            scored += 1

        # 剪刀差
        gi = int(np.searchsorted(gap_w_i8, end_i8, side="right"))
        if gi > 0:
            total += score_scissors(float(gap_w_v[gi - 1]))
            scored += 1

        # 公募新发
        ii = int(np.searchsorted(issue_w_i8, end_i8, side="right"))
        if ii >= 2:
            total += score_fund_issue(float(issue_w_v[ii - 1]), float(issue_w_v[ii - 2]))
            scored += 1

        # 减持
        ri = int(np.searchsorted(reduce_w_i8, end_i8, side="right"))
        if ri >= 2:
            total += score_holder_reduce(float(reduce_w_v[ri - 1]), float(reduce_w_v[ri - 2]))
            scored += 1

        # 行业资金
        ti = int(np.searchsorted(tech_w_i8, end_i8, side="right"))
        bi = int(np.searchsorted(bank_w_i8, end_i8, side="right"))
        if ti > 0 and bi > 0:
            total += score_industry_flow(float(tech_w_v[ti - 1]), float(bank_w_v[bi - 1]))
            scored += 1

        if scored == 0:
            continue
        total = max(-10, min(10, int(total)))
        out.append({"time": end_ts.strftime("%Y-%m-%d"), "value": total})
    return out


_TECH_BOARDS = {
    "半导体",
    "消费电子",
    "计算机设备",
    "软件开发",
    "通信设备",
    "光学光电子",
    "元件",
    "互联网服务",
}


def _weekly_sum(df: pd.DataFrame | None, value: str) -> pd.Series:
    x = _prep(df, [value])
    if x.empty or value not in x.columns:
        return pd.Series(dtype=float)
    s = pd.to_numeric(x[value], errors="coerce")
    out = x.assign(_v=s).dropna(subset=["_v"]).set_index("date").sort_index()["_v"]
    if out.empty:
        return out
    return out.resample("W-FRI").sum(min_count=1).dropna()


def _scissors_item(mf: pd.DataFrame | None) -> dict[str, Any]:
    x = _prep(mf, ["净额_亿元", "中单_亿元", "小单_亿元"])
    if x.empty or not {"净额_亿元", "中单_亿元", "小单_亿元"} <= set(x.columns):
        return _item("scissors", "剪刀差", score=None, note="主力行里还没有中单小单", status="gap")
    x = x.dropna(subset=["净额_亿元", "中单_亿元", "小单_亿元"])
    if len(x) < 20:
        return _item("scissors", "剪刀差", score=None, note=f"只有 {len(x)} 日", status="gap")
    gap = x["净额_亿元"] - (x["中单_亿元"] + x["小单_亿元"])
    weekly = gap.groupby(x["date"].dt.to_period("W-FRI")).sum()
    last = float(weekly.iloc[-1])
    day = str(weekly.index[-1].end_time.date())
    if last <= -3000:
        score, tag = -1, "周剪刀差到 3000 亿一档，计负分"
    else:
        score, tag = 0, "未到作者那档约 4000 亿"
    return _item("scissors", "剪刀差", score=score, note=f"{day} 当周 主力−(中单+小单) {last:.0f} 亿。{tag}", status="ok")


def _fund_issue_item(raw: pd.DataFrame | None) -> dict[str, Any]:
    weekly = _weekly_sum(raw, "shares_yi")
    if len(weekly) < 8:
        return _item("fund_issue", "公募新发", score=None, note="新成立份额不足 8 周", status="gap")
    last = float(weekly.iloc[-1])
    prev = float(weekly.iloc[-2])
    day = pd.Timestamp(weekly.index[-1]).date()
    if prev >= 300 and last <= prev * 0.5:
        score, tag = -1, "本周成立份额不到上周一半，且上周已在 300 亿以上"
    else:
        score, tag = 0, "未见腰斩式回落"
    return _item(
        "fund_issue",
        "公募新发",
        score=score,
        note=f"{day} 当周成立 {last:.0f} 亿份，上周 {prev:.0f}。{tag}。口径是已成立，不是认购中。",
        status="ok",
    )


def _holder_reduce_item(raw: pd.DataFrame | None) -> dict[str, Any]:
    weekly = _weekly_sum(raw, "amount_yi")
    if len(weekly) < 8:
        return _item("holder_reduce", "减持", score=None, note="减持金额不足 8 周", status="gap")
    last = float(weekly.iloc[-1])
    prev = float(weekly.iloc[-2])
    day = pd.Timestamp(weekly.index[-1]).date()
    if prev > 0 and last >= 50 and last >= prev * 1.75:
        score, tag = -1, "周金额比上周增 75% 以上"
    else:
        score, tag = 0, "周环比未到 75%"
    return _item(
        "holder_reduce",
        "减持",
        score=score,
        note=f"{day} 当周 {last:.0f} 亿，上周 {prev:.0f}。{tag}。按公告变动截止日，不是成交日。",
        status="ok",
    )


def _industry_flow_item(raw: pd.DataFrame | None) -> dict[str, Any]:
    x = _prep(raw, ["net_yi"])
    if x.empty or "name" not in x.columns:
        return _item("industry_flow", "行业资金", score=None, note="无行业资金流", status="gap")
    x = x.dropna(subset=["net_yi"])
    if x["date"].nunique() < 20:
        return _item("industry_flow", "行业资金", score=None, note="行业日频不足", status="gap")
    tech = x.loc[x["name"].isin(_TECH_BOARDS)]
    bank = x.loc[x["name"] == "银行"]
    if tech.empty or bank.empty:
        return _item("industry_flow", "行业资金", score=None, note="缺科技或银行板块", status="gap")
    tw = _weekly_sum(tech, "net_yi")
    bw = _weekly_sum(bank, "net_yi")
    if tw.empty or bw.empty:
        return _item("industry_flow", "行业资金", score=None, note="无法聚周", status="gap")
    last_t, last_b = float(tw.iloc[-1]), float(bw.iloc[-1])
    day = pd.Timestamp(tw.index[-1]).date()
    if last_t <= -80 and last_b >= -10:
        score, tag = -1, "科技一周净流出，银行没有接"
    else:
        score, tag = 0, "未见科技大出而银行不接"
    return _item(
        "industry_flow",
        "行业资金",
        score=score,
        note=f"{day} 科技合计 {last_t:.0f} 亿，银行 {last_b:.0f} 亿。{tag}。免费窗口约 120 日。",
        status="ok",
    )


def _ashare_crowding_item() -> dict[str, Any]:
    """A 股成交拥挤度：视频扩展位；有湖集中度算法前先占位。"""
    return _item(
        "ashare_crowding",
        "成交拥挤度",
        score=None,
        note="片子例：约 62.8% 成交在前 10% 个股。湖内集中度未上线，不进分。",
        status="gap",
    )


def _iv_spread_item() -> dict[str, Any]:
    return _item(
        "iv_spread",
        "隐含波动率",
        score=None,
        note="片子：科创50 IV 分位 ≫ 标普 VIX 分位 + 高杠杆 = 脆。暂无期权序列。",
        status="gap",
    )


def _us_crowding_item(us_crowding: dict[str, Any] | None) -> dict[str, Any]:
    """美股拥挤度扩展位（框架 §3.D 全球杠杆 + 隐含波动率 + 信用 + 估值）。"""
    if not us_crowding:
        return _item("us_crowding", "美股拥挤度", score=None, note="未获取", status="gap")
    margin_m2 = us_crowding.get("margin_m2_pct")
    vix_pct = us_crowding.get("vix_pct")
    sox_60d = us_crowding.get("sox_60d_ret")
    cape = us_crowding.get("shiller_cape")
    hy_ig = us_crowding.get("hy_ig_spread")
    hy_ig_pct = us_crowding.get("hy_ig_pct")
    vix_vxv = us_crowding.get("vix_vxv_ratio")
    margin_gdp = us_crowding.get("margin_gdp_pct")
    score = int(us_crowding.get("crowding_score", 0))
    verdict = us_crowding.get("verdict", "")
    parts: list[str] = []
    if margin_m2 is not None:
        tag = "危险" if margin_m2 >= 6.0 else ("偏高" if margin_m2 >= 5.0 else "正常")
        parts.append(f"保证金/M2={margin_m2:.1f}%({tag})")
    if margin_gdp is not None:
        parts.append(f"保证金/GDP={margin_gdp:.1f}%")
    if cape is not None:
        tag = "极端" if cape >= 35 else "高位"
        parts.append(f"CAPE={cape:.1f}({tag})")
    if vix_pct is not None:
        tag = "预警" if vix_pct >= 60 else "正常"
        parts.append(f"VIX分位={vix_pct:.0f}%({tag})")
    if vix_vxv is not None:
        struct = us_crowding.get("vix_structure", "")
        parts.append(f"VIX/VXV={vix_vxv:.2f}({struct})")
    if sox_60d is not None:
        parts.append(f"SOX 60d={sox_60d:+.1f}%")
    if hy_ig is not None and hy_ig_pct is not None:
        tag = "压力" if hy_ig_pct >= 80 else "正常"
        parts.append(f"HY-IG={hy_ig:.2f}%({hy_ig_pct:.0f}%分位,{tag})")
    note = "，".join(parts) + f"。评分{score}/6 {verdict}"
    # 只作参考，不计入 A 股打分
    return _item("us_crowding", "美股拥挤度", score=None, note=note, status="info")


def diagnose_position(bundle: dict[str, Any], *, heavy: bool = True) -> dict[str, Any]:
    """根据已入库序列给一个位置判断。结构对齐框架.md：三资金 → 行为 → 提前量 → 仓位带。

    heavy=False：跳过历史阶段表 / 评分曲线（各约数百 ms～1s+），供 GUI 读库快路径；
    完整表在「更新入库」或 payload 缓存未命中后的后台补全时再算。
    """
    margin_item = _margin_item(bundle.get("margin"))
    div_item, div_shape = _divergence_item(bundle.get("growth_value"))
    br_item, br_shape = _breadth_item(bundle.get("high_low"), bundle.get("weekly"))
    unlock_item, unlock_hot = _unlock_item(bundle.get("unlock_daily"))
    mf_item = _main_force_item(bundle.get("main_force_daily"))
    nt_item = _national_team_item(bundle.get("etf_monthly"))
    sc_item = _scissors_item(bundle.get("main_force_daily"))
    fi_item = _fund_issue_item(bundle.get("fund_issue"))
    hr_item = _holder_reduce_item(bundle.get("holder_reduce"))
    ind_item = _industry_flow_item(bundle.get("industry_flow_daily"))
    crowd_item = _ashare_crowding_item()
    iv_item = _iv_spread_item()
    us_item = _us_crowding_item(bundle.get("us_crowding"))
    # 展示顺序对齐视频：三资金 → 行为 → 解禁 → −9 扩展 → 全球旁证
    items = [
        nt_item,
        margin_item,
        mf_item,
        div_item,
        br_item,
        unlock_item,
        sc_item,
        fi_item,
        hr_item,
        ind_item,
        crowd_item,
        iv_item,
        us_item,
    ]
    gaps = [
        "主力长历史仍只有东财近端；周≥2000/−9 复刻依赖滚库",
        "国家队：宽基份额代理 ≠ 汇金/证金月变 ≠ ETF 净流入",
        "成交拥挤度（前10%成交占比）未进湖",
        "科创隐含波动率 vs VIX 分位未接",
        "产业二阶导（HBM 等涨价斜率）不进行情库",
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
    position["score"] = score
    position["nt_zone"] = nt_zone
    position["nt_trend"] = nt_trend
    position["margin_zone"] = m_zone
    position["margin_trend"] = m_trend
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
    periods: list[dict[str, Any]] = []
    if heavy:
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
    extend_items = [sc_item, fi_item, hr_item, ind_item]
    global_items = [crowd_item, iv_item, us_item]
    layers = [
        {
            "id": "funds",
            "title": "A. 资金三件套",
            "hint": "郭嘉队ETF仓位优先 · 融资分位×方向 · 主力只作风控",
            "items": fund_items,
        },
        {
            "id": "behavior",
            "title": "B. 行为两件套",
            "hint": "极值=分裂/买盘吃力；触发下跌仍看资金侧边际",
            "items": [div_item, br_item],
        },
        {
            "id": "ahead",
            "title": "C. 提前量",
            "hint": "解禁可提前约一年；高峰后 2–3 月主力易偏高",
            "items": [unlock_item],
        },
        {
            "id": "minus9",
            "title": "D. −9 周扩展项",
            "hint": "日常 ±1/±2；多项同负才打出极端分（片子 26·6 月第4周）",
            "items": extend_items,
        },
        {
            "id": "global",
            "title": "E. 拥挤 / 波动 / 外盘",
            "hint": "红灯旁证；有数展示，默认不进 A 股合计分",
            "items": global_items,
        },
    ]

    # 视频「红灯四件套」：偏离极值 + 广度背离 + 高杠杆 + 外盘脆弱
    us_fragile = False
    us_raw = bundle.get("us_crowding") if isinstance(bundle.get("us_crowding"), dict) else None
    if us_item.get("status") in {"info", "ok"} and us_raw:
        try:
            us_fragile = int(us_raw.get("crowding_score") or 0) >= 4
        except (TypeError, ValueError):
            us_fragile = False
        if not us_fragile and us_raw.get("margin_m2_pct") is not None:
            try:
                us_fragile = float(us_raw["margin_m2_pct"]) >= 5.5
            except (TypeError, ValueError):
                pass
    red_flags = [
        {
            "id": "div",
            "label": "偏离极值",
            "on": div_shape == "extreme",
            "note": "≈17 或分位顶端",
        },
        {
            "id": "breadth",
            "label": "高低比背离",
            "on": br_shape in {"cold", "diverge"},
            "note": "热~10:1 / 空~1:10；涨中极冷=背离",
        },
        {
            "id": "leverage",
            "label": "高杠杆",
            "on": m_zone == "high",
            "note": "融资分位高位（场外杠杆看不见）",
        },
        {
            "id": "global",
            "label": "外盘脆弱",
            "on": us_fragile,
            "note": "保证金/M2≈6% 或拥挤评分偏高",
        },
    ]
    red_on = sum(1 for f in red_flags if f["on"])
    red_light = {
        "flags": red_flags,
        "lit": red_on,
        "severe": red_on >= 3 and funds_worse,
        "note": (
            f"红灯 {red_on}/4 亮"
            + (" · 且资金侧在变差 → 严肃对待" if red_on >= 3 and funds_worse else "")
            + (" · 扳机仍是资金边际，不是偏离本身" if red_on else "")
        ),
    }

    if div_shape == "narrowing":
        k_type = {
            "mode": "切价值",
            "hint": "偏离从高位收窄：大资金成长→价值，顺风切换，不宜教条死守。",
        }
    elif div_shape == "extreme":
        k_type = {
            "mode": "K型走阔",
            "hint": "成长虹吸价值、偏离走阔：高拥挤资产对二阶导敏感，财报前易躲。",
        }
    else:
        k_type = {
            "mode": "中性",
            "hint": "偏离未到极值；K型切换信号弱。",
        }

    score_logic = (
        "散户决策：盯其他几路资金重大改变。"
        "权重 郭嘉队ETF > 融资分位×方向 > 主力/行为/解禁/−9扩展。"
        "日常约 ±1/±2；多项同负才极端（如 −9）。"
        "硬信号优先 → 离场/减仓/持有/加仓/抄底。缺的不记 0。"
    )

    return {
        "state": state,
        "summary": summary,
        "score": score,
        "score_note": f"已打分 {len(scored)} 项，合计 {score:+d}。缺的不记 0。",
        "score_logic": score_logic,
        "framework": "散户决策 · 视频全量口径 · 打分出仓位",
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
        "red_light": red_light,
        "k_type": k_type,
        "triple": triple,
        "resonance": resonance,
        "funds_month": _funds_month_table(bundle),
        "nodes": nodes,
        "gaps": gaps,
        "position": position,
        "phases": phases,
        "periods": periods,
        "score_curve": score_history_weekly(bundle) if heavy else [],
        "heavy": bool(heavy),
    }
