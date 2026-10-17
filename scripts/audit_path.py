# -*- coding: utf-8 -*-
"""One-shot audit of DP cycle deduction path. Run from repo with PYTHONPATH=src."""
from __future__ import annotations

from pathlib import Path
import sys
_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / 'src'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pandas as pd
from data_source.dp_cycle.gui_bundle import load_dp_cycle_gui_bundle
from data_source.dp_cycle.diagnosis import (
    diagnose_position,
    score_margin_regime,
    score_national_team_regime,
    _as_pct,
)


def cov(name, df):
    if df is None or getattr(df, "empty", True):
        return f"{name}: EMPTY"
    n = len(df)
    if "date" not in df.columns:
        return f"{name}: n={n}"
    d0 = str(pd.to_datetime(df["date"]).min().date())
    d1 = str(pd.to_datetime(df["date"]).max().date())
    return f"{name}: n={n} {d0}~{d1}"


def main() -> None:
    b = load_dp_cycle_gui_bundle(refresh=False)
    d = diagnose_position(b)
    issues: list[str] = []

    print("=== COVERAGE ===")
    for k in [
        "weekly",
        "margin",
        "etf_monthly",
        "etf_daily",
        "main_force_daily",
        "growth_value",
        "high_low",
        "unlock_daily",
    ]:
        print(cov(k, b.get(k)))

    print("\n=== ITEMS ===")
    for it in d["items"]:
        z = it.get("zone") or "-"
        t = it.get("trend") or it.get("direction") or "-"
        print(
            f"{it['id']:14} score={str(it['score']):4} status={it['status']:5} "
            f"zone={z:5} trend={t}"
        )
        print("  ", (it.get("note") or "")[:140])

    print("\n=== PATH ===")
    print("state:", d["state"])
    print("decision:", d["decision"]["label"], d["decision"]["equity_text"])
    print("why:", d["decision"]["why"])
    print("phase:", d["position"]["stage"], d["position"]["actor"])
    print("score:", d["score"])
    print("resonance:", d["resonance"]["text"])

    nt = next(i for i in d["items"] if i["id"] == "national_team")
    mg = next(i for i in d["items"] if i["id"] == "margin")

    if nt.get("zone") == "high" and nt.get("trend") == "out" and d["decision"]["id"] != "exit":
        issues.append("FAIL: NT high out should be exit")
    if mg.get("zone") == "high" and mg.get("trend") == "up" and d["decision"]["id"] not in ("exit", "reduce"):
        issues.append("FAIL: margin high up should reduce/exit")
    if mg.get("zone") == "high" and mg.get("trend") == "down" and d["decision"]["id"] not in ("exit", "reduce"):
        issues.append("FAIL: margin high down should reduce")
    s = sum(int(i["score"]) for i in d["items"] if i["score"] is not None)
    if s != d["score"]:
        issues.append(f"FAIL: score sum {s} != {d['score']}")
    if d["decision"]["equity_text"] != d["position"]["equity_text"]:
        issues.append(
            f"WARN: decision equity {d['decision']['equity_text']} vs position {d['position']['equity_text']}"
        )

    # §5 table vs decision equity for nt_sell / margin_down
    # Framework §5 nt_sell was 30-40%; code now 0-20% for exit — document as intentional retail override
    if d["position"]["phase_id"] == "margin_down":
        # decision reduce 20-40 vs phase sleeve 20-30 — ok if decision wins
        pass

    em = b.get("etf_monthly")
    if isinstance(em, pd.DataFrame) and not em.empty:
        e = em.copy()
        e["份额_亿份"] = pd.to_numeric(e["份额_亿份"], errors="coerce")
        e = e.dropna(subset=["份额_亿份"])
        shares = float(e["份额_亿份"].iloc[-1])
        pct = float((e["份额_亿份"] <= shares).mean() * 100)
        delta = float(pd.to_numeric(e["月变动_亿份"], errors="coerce").iloc[-1])
        sc, z, t, _tag = score_national_team_regime(pct, delta)
        print(f"\nNT recalc: shares={shares:.1f} pct={pct:.1f} delta={delta:+.1f} -> {sc} {z} {t}")
        if sc != nt["score"] or z != nt.get("zone"):
            issues.append(f"FAIL: NT mismatch {nt['score']}/{nt.get('zone')} vs {sc}/{z}")

    m = b["margin"].dropna(subset=["融资余额_亿元"])
    last = m.iloc[-1]
    prev = m.iloc[-21]
    delta = float(last["融资余额_亿元"] - prev["融资余额_亿元"])
    pct = _as_pct(float(last["分位_全样本"]))
    sc, z, t, _tag = score_margin_regime(pct, delta)
    print(f"Margin recalc: bal={last['融资余额_亿元']:.0f} pct={pct:.1f} delta21={delta:+.0f} -> {sc} {z} {t}")
    if sc != mg["score"] or z != mg.get("zone"):
        issues.append(f"FAIL: Margin mismatch {mg['score']}/{mg.get('zone')} vs {sc}/{z}")

    # Precision notes
    print("\n=== PRECISION NOTES ===")
    mf = b.get("main_force_daily")
    if mf is None or getattr(mf, "empty", True) or len(mf) < 20:
        print("MAIN_FORCE: gap — cannot score weekly outflow (author compass incomplete)")
    if nt.get("zone") == "mid":
        print(
            "NT_ZONE: mid — expanding percentile on short ETF history can understate "
            "'high inventory' vs author Huijin/CSFC levels"
        )
    if pct is not None and pct >= 75 and t == "down":
        print("MARGIN: high+down path OK for 杀杠杆/减仓 (user rule: high+up=leave, high+down=delever)")

    # Doc conflicts
    print("\n=== DOC/CODE TENSIONS ===")
    print(
        "1. 框架§5 顶部降温仓位 30-40% vs §6/代码 离场 0-20% — 散户决策覆盖阶段表（刻意）"
    )
    print(
        "2. 框架§3 杠杆原口径看月变动±1000；代码已改分位×方向 — §6 为准"
    )
    print(
        "3. 国家队正口径=汇金证金；代码=宽基份额分位 — 代理误差需写明"
    )
    print(
        "4. README 仍写「诊断大盘位置」；产品已升级为散户决策 — 需同步"
    )

    print("\n=== ISSUES ===")
    print("\n".join(issues) if issues else "none (path checks passed)")
    print("\ngaps:", " | ".join(d["gaps"]))


if __name__ == "__main__":
    main()

