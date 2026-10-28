# -*- coding: utf-8 -*-
"""资金行为路径上的仓位带 + 散户决策。

对照 research/DP/资金行为.jpg：
黑字 = 上涨段谁在买，红字 = 下跌段谁在卖。
你是散户：盯其他几路资金重大改变，打分后落到 离场/减仓/持有/加仓/抄底。

仓位是风险带，不是下单指令。缺项不当成安全。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from data_source.dp_cycle.rules import (  # noqa: E402
    DIVERGENCE_EXTREME,
    HL_COLD_RATIO,
    HL_HOT_RATIO,
    NT_SHORT_WINDOW,
    breadth_shape,
    decide_retail_action_core,
    divergence_shape,
    score_breadth,
    score_divergence,
    score_margin_regime,
    score_national_team_regime,
    validate_rule_version,
)

# 五档决策（页面首屏）
DECISIONS: list[dict[str, Any]] = [
    {
        "id": "exit",
        "label": "离场",
        "equity_low": 0,
        "equity_high": 20,
        "tone": "danger",
        "hint": "其他资金高位撤退。别接盘。",
    },
    {
        "id": "reduce",
        "label": "减仓",
        "equity_low": 20,
        "equity_high": 40,
        "tone": "warn",
        "hint": "风险在升或杀杠杆。先降暴露。",
    },
    {
        "id": "hold",
        "label": "持有观望",
        "equity_low": 40,
        "equity_high": 55,
        "tone": "neutral",
        "hint": "证据打架。底仓，不猜方向。",
    },
    {
        "id": "add",
        "label": "加仓",
        "equity_low": 60,
        "equity_high": 70,
        "tone": "good",
        "hint": "低位资金在跟。可加，不追热点。",
    },
    {
        "id": "bottom",
        "label": "抄底",
        "equity_low": 60,
        "equity_high": 80,
        "tone": "good",
        "hint": "郭嘉队低位托举。宽基分批，勿听恐吓离场。",
    },
]
_DECISION_BY_ID = {row["id"]: row for row in DECISIONS}

PHASES: list[dict[str, Any]] = [
    {
        "id": "nt_buy",
        "actor": "郭嘉队",
        "side": "buy",
        "stage": "启动",
        "equity_low": 60,
        "equity_high": 80,
        "decision": "bottom",
        "sleeve": "他们在底部买。散户可抄底宽基，别被低位恐吓吓跑。",
    },
    {
        "id": "margin_up",
        "actor": "融资",
        "side": "buy",
        "stage": "推进",
        "equity_low": 60,
        "equity_high": 70,
        "decision": "add",
        "sleeve": "低位融资在跟。可加仓留趋势，波动会大。",
    },
    {
        "id": "inst_up",
        "actor": "保险·ETF·公募",
        "side": "buy",
        "stage": "机构参与",
        "equity_low": 60,
        "equity_high": 70,
        "decision": "add",
        "sleeve": "机构进场段。顺风可以，不追已经很热的。",
    },
    {
        "id": "retail_up",
        "actor": "散户",
        "side": "buy",
        "stage": "过热接盘",
        "equity_low": 40,
        "equity_high": 50,
        "decision": "reduce",
        "sleeve": "高位唱多、散户接盘段。你要反向：减仓不追。",
    },
    {
        "id": "nt_sell",
        "actor": "郭嘉队",
        "side": "sell",
        "stage": "顶部降温",
        "equity_low": 0,
        "equity_high": 20,
        "decision": "exit",
        "sleeve": "他们在顶部卖。最高优先：离场，别接盘。",
    },
    {
        "id": "margin_down",
        "actor": "融资",
        "side": "sell",
        "stage": "杀杠杆",
        "equity_low": 20,
        "equity_high": 30,
        "decision": "reduce",
        "sleeve": "高位去杠杆。只留底仓，余额没止住不抄。",
    },
    {
        "id": "inst_sell",
        "actor": "保险·ETF·公募",
        "side": "sell",
        "stage": "切向价值",
        "equity_low": 30,
        "equity_high": 40,
        "decision": "reduce",
        "sleeve": "机构在切。少成长、多价值，仓位先降一档。",
    },
    {
        "id": "retail_out",
        "actor": "散户",
        "side": "sell",
        "stage": "散户离场",
        "equity_low": 20,
        "equity_high": 30,
        "decision": "reduce",
        "sleeve": "散户在砍。常见靠近一段低点，杠杆没止住仍不加。",
    },
    {
        "id": "unclear",
        "actor": "证据不齐",
        "side": "mixed",
        "stage": "震荡",
        "equity_low": 40,
        "equity_high": 55,
        "decision": "hold",
        "sleeve": "方向不明。底仓观望，不猜。",
    },
]

_BY_ID = {row["id"]: row for row in PHASES}


def phase_by_id(phase_id: str) -> dict[str, Any]:
    return dict(_BY_ID.get(phase_id) or _BY_ID["unclear"])


def choose_phase(
    *,
    margin_zone: str = "gap",
    margin_trend: str = "flat",
    nt_zone: str = "gap",
    nt_trend: str = "flat",
    div_shape: str = "gap",
    br_shape: str = "gap",
    unlock_hot: bool = False,
    mf_score: int | None = None,
    nt_score: int | None = None,
    leverage: int | None = None,  # 兼容旧调用：仅作弱后备
    rule_version: str = "improved_v1",
) -> str:
    """把已有打分落到资金行为图的某一段。郭嘉队仓位优先，融资看分位×方向。"""
    validate_rule_version(rule_version)
    if rule_version == "author_v1" and nt_zone == "high" and nt_trend == "out":
        return "nt_sell"
    if rule_version == "author_v1" and nt_zone == "low" and nt_trend == "in":
        return "nt_buy"
    if rule_version == "author_v1" and nt_zone == "high" and nt_trend in {"in", "flat"}:
        return "retail_up"
    if margin_zone == "high" and margin_trend == "up":
        return "retail_up"
    if margin_zone == "high" and margin_trend == "down":
        return "margin_down"
    if margin_zone == "low" and margin_trend == "up":
        return "margin_up"
    if margin_zone == "low" and margin_trend == "down":
        return "margin_down"
    if div_shape == "narrowing":
        return "inst_sell"
    if unlock_hot and margin_zone == "high":
        return "margin_down"
    if mf_score is not None and mf_score <= -2 and margin_zone == "high":
        return "margin_down"
    if rule_version == "author_v1" and nt_score is not None and nt_score <= -2:
        return "nt_sell"
    # 旧参数兜底
    if leverage is not None and leverage <= -2:
        return "margin_down"
    if leverage is not None and leverage >= 2:
        return "margin_up"
    if br_shape in {"cold", "diverge"} and margin_zone == "high":
        return "margin_down"
    return "unclear"


_BASIS = {
    "nt_sell": "郭嘉队ETF 高仓位还在减",
    "nt_buy": "郭嘉队ETF 低仓位加仓",
    "retail_up": "融资或国家队高位拥挤，过热段",
    "margin_down": "融资高位去杠杆，或低位跟随偏空",
    "margin_up": "融资低位加杠杆，跟随",
    "inst_sell": "偏离从高位往下",
    "unclear": "郭嘉队/融资/偏离没落到同一段",
}


def allocation_view(phase_id: str, *, partial: bool, rule_version: str = "improved_v1") -> dict[str, Any]:
    validate_rule_version(rule_version)
    if rule_version != "author_v1" and phase_id in {"nt_buy", "nt_sell"}:
        phase_id = "unclear"
    row = phase_by_id(phase_id)
    low, high = int(row["equity_low"]), int(row["equity_high"])
    note = row["sleeve"]
    if partial and row["id"] in {"margin_down", "margin_up", "unclear"}:
        note += " 主力长历史还没有。"
    return {
        "phase_id": row["id"],
        "stage": row["stage"],
        "actor": row["actor"],
        "side": row["side"],
        "equity_low": low,
        "equity_high": high,
        "equity_text": f"{low}–{high}%" if low != high else f"{low}%",
        "sleeve": note,
        "basis": (
            {"retail_up": "融资高位拥挤，过热段", "unclear": "融资与行为指标未落到明确阶段"}.get(row["id"])
            if rule_version != "author_v1" and row["id"] in {"retail_up", "unclear"}
            else _BASIS.get(row["id"], "这项没有直接序列")
        ),
        "decision": row.get("decision") or "hold",
    }


def decide_retail_action(
    *,
    score: int,
    nt_zone: str,
    nt_trend: str,
    margin_zone: str,
    margin_trend: str,
    phase_id: str,
    behavior_bad: bool = False,
    rule_version: str = "improved_v1",
) -> dict[str, Any]:
    """散户决策：硬信号优先，再看分数带。结论=离场/减仓/持有观望/加仓/抄底。"""
    validate_rule_version(rule_version)
    reasons: list[str] = []
    if rule_version != "author_v1" and phase_id in {"nt_buy", "nt_sell"}:
        phase_id = "unclear"
    action_id = decide_retail_action_core(
        score=score,
        nt_zone=nt_zone,
        nt_trend=nt_trend,
        margin_zone=margin_zone,
        margin_trend=margin_trend,
        behavior_bad=behavior_bad,
        rule_version=rule_version,
    )
    if action_id is None:
        # 落到资金行为阶段的默认决策
        phase = phase_by_id(phase_id)
        action_id = str(phase.get("decision") or "hold")
        reasons.append(f"落在资金行为「{phase.get('stage')}」段")
    else:
        # 硬信号原因
        if rule_version == "author_v1" and action_id == "exit" and nt_zone == "high" and nt_trend == "out":
            reasons.append("郭嘉队高仓位还在减（最高优先）")
        elif rule_version == "author_v1" and action_id == "reduce" and nt_zone == "high":
            reasons.append("郭嘉队高仓位拥挤/钝化，高位唱多段勿接")
        elif action_id == "exit" and margin_zone == "high" and margin_trend == "up":
            reasons.append("融资高位还在加，风险升，警惕离开")
        elif action_id == "reduce" and margin_zone == "high" and margin_trend == "down":
            reasons.append("融资高位去杠杆，杀杠杆未结束")
        elif rule_version == "author_v1" and action_id == "bottom" and nt_zone == "low" and nt_trend == "in":
            reasons.append("郭嘉队低位加仓，托举段可抄底宽基")
        elif action_id == "add" and margin_zone == "low" and margin_trend == "up":
            reasons.append("融资低位加杠杆，跟随加仓")
        elif action_id in {"exit", "reduce"}:
            reasons.append(f"合计分 {score:+d}，偏防守")
        elif action_id in {"add", "bottom"}:
            reasons.append(f"合计分 {score:+d}，偏进攻")

    row = dict(_DECISION_BY_ID.get(action_id) or _DECISION_BY_ID["hold"])
    # 仓位带：硬离场用决策带；否则与阶段带取更保守一侧（卖侧取低，买侧取阶段）
    phase = phase_by_id(phase_id)
    if row["id"] in {"exit", "reduce"}:
        low, high = int(row["equity_low"]), int(row["equity_high"])
    elif row["id"] in {"add", "bottom"}:
        low, high = int(row["equity_low"]), int(row["equity_high"])
    else:
        low, high = int(phase["equity_low"]), int(phase["equity_high"])

    return {
        "id": row["id"],
        "label": row["label"],
        "tone": row["tone"],
        "hint": row["hint"],
        "equity_low": low,
        "equity_high": high,
        "equity_text": f"{low}–{high}%" if low != high else f"{low}%",
        "why": "；".join(reasons) if reasons else row["hint"],
        "watch": "盯其他几路资金行为有没有重大改变，再决定加减。",
    }


def _as_of_month(frame: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True) or "date" not in frame.columns:
        return pd.DataFrame()
    if any(col not in frame.columns for col in value_cols):
        return pd.DataFrame()
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    for col in value_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["date"]).sort_values("date")
    return out.groupby("date", as_index=False).last()


def monthly_regimes(
    bundle: dict[str, Any], *, rule_version: str = "improved_v1", as_of: Any | None = None
) -> list[dict[str, Any]]:
    """每个月末用和当前诊断同一套窗口打分，相邻同阶段合并。"""
    validate_rule_version(rule_version)
    from data_source.dp_cycle.margin import (
        _margin_local_timestamps, causal_margin_frame, margin_signal_at, margin_signal_from_row,
    )

    raw_m = bundle.get("margin") if isinstance(bundle.get("margin"), pd.DataFrame) else pd.DataFrame()
    if "available_at" in raw_m.columns:
        raw_m = raw_m.copy()
        raw_m["available_at"] = _margin_local_timestamps(raw_m["available_at"])
    margin = causal_margin_frame(raw_m, as_of=as_of, trade_calendar=bundle.get("trade_calendar"))
    if margin.empty or "融资余额_亿元" not in margin.columns:
        return []

    div = _as_of_month(bundle.get("growth_value") if isinstance(bundle.get("growth_value"), pd.DataFrame) else pd.DataFrame(), ["偏离度"])
    hl = _as_of_month(
        bundle.get("high_low") if isinstance(bundle.get("high_low"), pd.DataFrame) else pd.DataFrame(),
        ["new_high", "new_low"],
    )
    month_last = margin.groupby(margin["date"].dt.to_period("M"), as_index=False).tail(1)

    etf_m = bundle.get("etf_monthly") if isinstance(bundle.get("etf_monthly"), pd.DataFrame) else pd.DataFrame()
    etf = pd.DataFrame()
    if isinstance(etf_m, pd.DataFrame) and not etf_m.empty and "份额_亿份" in etf_m.columns:
        etf = etf_m.copy()
        etf["date"] = pd.to_datetime(etf["date"], errors="coerce")
        etf["份额_亿份"] = pd.to_numeric(etf["份额_亿份"], errors="coerce")
        if "月变动_亿份" in etf.columns:
            etf["月变动_亿份"] = pd.to_numeric(etf["月变动_亿份"], errors="coerce")
        etf = etf.dropna(subset=["date", "份额_亿份"]).sort_values("date")

    rows: list[dict[str, Any]] = []
    for end in month_last["date"]:
        end = pd.Timestamp(end).normalize()
        loc = int(margin.index[margin["date"] == end][-1])
        latest = margin.loc[loc]
        released = pd.to_datetime(latest.get("available_at"), errors="coerce")
        missing_visible = pd.isna(latest["融资余额_亿元"]) and (pd.isna(released) or released <= end)
        signal = (margin_signal_from_row(latest) if missing_visible else
                  margin_signal_at(raw_m, as_of=end, trade_calendar=bundle.get("trade_calendar"))
                  if "available_at" in margin.columns else margin_signal_from_row(latest))
        if signal["status"] != "ok":
            view = allocation_view("unclear", partial=True, rule_version=rule_version)
            rows.append({"date": end.strftime("%Y-%m-%d"), "phase_id": "unclear", "stage": "数据不足",
                         "actor": view["actor"], "side": view["side"], "equity_text": "",
                         "basis": signal["reason"], "leverage": None, "margin_status": signal["status"],
                         "margin_pct": None, "margin_delta_20": None, "margin_observation_date": None})
            continue
        lev, m_zone, m_trend = signal["score"], signal["zone"], signal["trend"]

        nt_zone, nt_trend, nt_score = "gap", "flat", None
        if rule_version == "author_v1" and not etf.empty:
            past_e = etf.loc[etf["date"] <= end]
            consecutive = len(past_e) >= 2 and past_e["date"].iloc[-2].to_period("M") + 1 == past_e["date"].iloc[-1].to_period("M")
            if len(past_e) >= 6 and consecutive:
                shares = float(past_e["份额_亿份"].iloc[-1])
                # 双轨分位
                pct_all = float((past_e["份额_亿份"] <= shares).mean() * 100.0)
                short_hist = past_e["份额_亿份"].tail(NT_SHORT_WINDOW)
                pct_short = float((short_hist <= shares).mean() * 100.0) if len(short_hist) >= 12 else pct_all
                pct_nt = max(pct_all, pct_short) if pct_all >= 50 else min(pct_all, pct_short)
                if "月变动_亿份" in past_e.columns:
                    d_nt = float(past_e["月变动_亿份"].iloc[-1]) if pd.notna(past_e["月变动_亿份"].iloc[-1]) else None
                else:
                    d_nt = shares - float(past_e["份额_亿份"].iloc[-2])
                if d_nt is not None:
                    nt_score, nt_zone, nt_trend, _ = score_national_team_regime(pct_nt, d_nt)

        div_now = None
        div_prev = None
        if not div.empty and "偏离度" in div.columns:
            past = div.loc[div["date"] <= end].dropna(subset=["偏离度"])
            if not past.empty:
                div_now = float(past["偏离度"].iloc[-1])
                earlier = past.loc[past["date"] <= end - pd.Timedelta(days=60)]
                if not earlier.empty:
                    div_prev = float(earlier["偏离度"].iloc[-1])
        div_shape = "gap" if div_now is None else divergence_shape(div_now, div_prev)
        br_shape = "gap"
        if not hl.empty and {"new_high", "new_low"}.issubset(hl.columns):
            tail = hl.loc[hl["date"] <= end].dropna(subset=["new_high", "new_low"]).tail(5)
            if not tail.empty:
                ratio = float(tail["new_high"].mean()) / max(float(tail["new_low"].mean()), 1.0)
                br_shape = breadth_shape(ratio, False)
        phase_id = choose_phase(
            margin_zone=m_zone,
            margin_trend=m_trend,
            nt_zone=nt_zone,
            nt_trend=nt_trend,
            div_shape=div_shape,
            br_shape=br_shape,
            unlock_hot=False,
            mf_score=None,
            nt_score=nt_score,
            rule_version=rule_version,
        )
        view = allocation_view(phase_id, partial=True, rule_version=rule_version)
        rows.append(
            {
                "date": end.strftime("%Y-%m-%d"),
                "phase_id": phase_id,
                "stage": view["stage"],
                "actor": view["actor"],
                "side": view["side"],
                "equity_text": view["equity_text"],
                "basis": view["basis"],
                "leverage": lev,
                "margin_status": "ok",
                "margin_pct": signal["pct"],
                "margin_delta_20": signal["delta"],
                "margin_observation_date": signal["date"].strftime("%Y-%m-%d"),
            }
        )
    if not rows:
        return []
    segments: list[dict[str, Any]] = []
    cur = dict(rows[0])
    cur["start"] = cur["date"]
    cur["end"] = cur["date"]
    for row in rows[1:]:
        if row["phase_id"] == cur["phase_id"] and row["margin_status"] == cur["margin_status"]:
            cur["end"] = row["date"]
            for key in ("leverage", "margin_status", "margin_pct", "margin_delta_20", "margin_observation_date"):
                cur[key] = row[key]
        else:
            segments.append(cur)
            cur = dict(row)
            cur["start"] = row["date"]
            cur["end"] = row["date"]
    segments.append(cur)
    for seg in segments:
        seg.pop("date", None)
    return segments
