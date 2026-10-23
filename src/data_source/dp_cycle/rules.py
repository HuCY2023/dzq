# -*- coding: utf-8 -*-
"""大周期散户决策 · 纯打分规则。

从 diagnosis.py 抽出，零 IO，方便单测与事件回测复用。
口径权威：research/DP/大周期/框架.md。
"""
from __future__ import annotations

import pandas as pd

# ── 阈值 ──────────────────────────────────────────────
MARGIN_PCT_HIGH = 75.0
MARGIN_PCT_LOW = 40.0
MARGIN_DELTA_FLAT = 300.0

NT_PCT_HIGH = 75.0
NT_PCT_LOW = 40.0
NT_DELTA_FLAT = 5.0
NT_SHORT_WINDOW = 36  # 近 N 月分轨窗口

DIVERGENCE_EXTREME = 16.0
HL_HOT_RATIO = 8.0
HL_COLD_RATIO = 0.2
UNLOCK_HOT_MULT = 1.5
MAIN_FORCE_WEEK_EXTREME = -2000.0
SCISSORS_WEEK_EXTREME = -3000.0


def _zone(pct: float | None, high: float, low: float) -> str:
    if pct is None:
        return "gap"
    if pct >= high:
        return "high"
    if pct <= low:
        return "low"
    return "mid"


# ── 融资：分位 × 方向 ─────────────────────────────────
def score_margin_regime(
    pct: float | None, delta: float
) -> tuple[int, str, str, str]:
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


# ── 郭嘉队ETF：仓位分位优先 ───────────────────────────
def score_national_team_regime(
    pct: float | None, delta: float
) -> tuple[int, str, str, str]:
    """返回 (score, zone, trend, tag)。trend: in/out/flat。"""
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


# ── 散户决策硬信号 ────────────────────────────────────
def decide_retail_action_core(
    *,
    score: int,
    nt_zone: str,
    nt_trend: str,
    margin_zone: str,
    margin_trend: str,
    behavior_bad: bool = False,
) -> str | None:
    """硬信号 → 决策 id；返回 None 表示落到阶段默认。"""
    if nt_zone == "high" and nt_trend == "out":
        return "exit"
    if nt_zone == "high" and nt_trend in {"in", "flat"}:
        return "reduce"
    if margin_zone == "high" and margin_trend == "up":
        return "exit" if score <= -3 or behavior_bad else "reduce"
    if margin_zone == "high" and margin_trend == "down":
        return "reduce"
    if nt_zone == "low" and nt_trend == "in" and margin_zone in {"low", "mid", "gap"}:
        return "bottom"
    if margin_zone == "low" and margin_trend == "up" and score >= 2:
        return "add"
    if score <= -4:
        return "exit"
    if score <= -2:
        return "reduce"
    if score >= 3 and nt_zone == "low":
        return "bottom"
    if score >= 2:
        return "add"
    return None


# ── 扩展打分项（score_history_weekly 复用） ──────────
def score_unlock_hot(this_val: float | None, next_val: float | None, base: float | None) -> bool:
    """本月或下月解禁 ≥ 近 24 完整月中位数 1.5 倍。"""
    return bool(
        base
        and (
            (this_val is not None and this_val >= base * UNLOCK_HOT_MULT)
            or (next_val is not None and next_val >= base * UNLOCK_HOT_MULT)
        )
    )


def score_scissors(weekly_gap: float) -> int:
    """周剪刀差 ≤ −3000 亿 → −1。"""
    return -1 if weekly_gap <= SCISSORS_WEEK_EXTREME else 0


def score_fund_issue(last: float, prev: float) -> int:
    """公募新发：上周 ≥300 且本周不到一半 → −1。"""
    return -1 if prev >= 300 and last <= prev * 0.5 else 0


def score_holder_reduce(last: float, prev: float) -> int:
    """减持：本周 ≥50 亿且比上周增 75% → −1。"""
    return -1 if prev > 0 and last >= 50 and last >= prev * 1.75 else 0


def score_industry_flow(tech_weekly: float, bank_weekly: float) -> int:
    """行业资金：科技 ≤ −80 且银行 ≥ −10 → −1。"""
    return -1 if tech_weekly <= -80 and bank_weekly >= -10 else 0


# ── 偏离度形态 ────────────────────────────────────────
def divergence_shape(last: float, prev: float | None) -> str:
    """返回 extreme / narrowing / calm。"""
    if last >= DIVERGENCE_EXTREME:
        return "extreme"
    if prev is not None and prev >= DIVERGENCE_EXTREME - 1 and last < prev - 0.4:
        return "narrowing"
    return "calm"


def score_divergence(shape: str) -> int:
    return -1 if shape in {"extreme", "narrowing"} else 0


# ── 广度形态 ──────────────────────────────────────────
def breadth_shape(ratio: float, index_up: bool) -> str:
    if ratio >= HL_HOT_RATIO:
        return "hot"
    if ratio <= HL_COLD_RATIO and index_up:
        return "diverge"
    if ratio <= HL_COLD_RATIO:
        return "cold"
    return "mixed"


def score_breadth(shape: str) -> int:
    if shape == "hot":
        return 1
    if shape in {"cold", "diverge"}:
        return -1
    return 0
