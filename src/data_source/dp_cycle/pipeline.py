# -*- coding: utf-8 -*-
"""大周期流水线：优先 DuckDB(2015+)，出 CSV/PNG/PDF。"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import pandas as pd

from data_source.dp_cycle import report
from data_source.dp_cycle.gui_bundle import DEFAULT_WEEKLY_INDEX, load_dp_cycle_gui_bundle
from data_source.dp_cycle.long_term_pdf import build_long_term_pdf
from data_source.dp_cycle.store import HISTORY_START_STR


def _save_csv(df: pd.DataFrame, path: str) -> None:
    if df is None or df.empty:
        return
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False, encoding="utf-8-sig")


def run_dp_cycle_pipeline(
    *,
    out_dir: str | None = None,
    for_date: date | None = None,
    high_low_start: str = HISTORY_START_STR,
    margin_lookback_years: int = 5,
    refresh_db: bool = True,
    skip_high_low: bool = False,
    index_symbol: str = DEFAULT_WEEKLY_INDEX,
    bundle: dict[str, Any] | None = None,
    diagnosis: dict[str, Any] | None = None,
    publish_latest: bool = True,
) -> dict[str, Any]:
    """更新/读取库 → 落盘图表与长期跟踪 PDF。"""
    del margin_lookback_years
    from output_paths import dp_cycle_dir, dp_cycle_store_dir, project_root

    out = out_dir or dp_cycle_dir(for_date)
    os.makedirs(out, exist_ok=True)

    if bundle is None:
        bundle = load_dp_cycle_gui_bundle(
            index_symbol=index_symbol,
            high_low_start=high_low_start,
            as_of=for_date,
            refresh=refresh_db,
            skip_high_low=skip_high_low,
            mode="pipeline",
        )
    meta: dict[str, Any] = {
        "out_dir": out,
        "history_start": HISTORY_START_STR,
        "indicators": bundle.get("meta") or {},
    }
    png_map: dict[str, str] = {}

    mf_agg = {
        "daily": bundle.get("main_force_daily", pd.DataFrame()),
        "weekly": bundle.get("main_force_weekly", pd.DataFrame()),
        "monthly": bundle.get("main_force_monthly", pd.DataFrame()),
    }
    _save_csv(mf_agg["daily"], os.path.join(out, "01_main_force_daily.csv"))
    _save_csv(mf_agg["weekly"], os.path.join(out, "01_main_force_weekly.csv"))
    _save_csv(mf_agg["monthly"], os.path.join(out, "01_main_force_monthly.csv"))
    p = report.plot_main_force(mf_agg, os.path.join(out, "01_main_force.png"))
    if p:
        png_map["01_main_force"] = p

    etf_hist = bundle.get("etf_hist", pd.DataFrame())
    etf_m = bundle.get("etf_monthly", pd.DataFrame())
    _save_csv(etf_hist, os.path.join(out, "02_etf_shares_daily.csv"))
    _save_csv(etf_m, os.path.join(out, "02_etf_shares_monthly.csv"))
    p = report.plot_national_team(bundle.get("etf_hist_adjusted", pd.DataFrame()), etf_m, os.path.join(out, "02_national_team.png"))
    if p:
        png_map["02_national_team"] = p

    margin = bundle.get("margin", pd.DataFrame())
    _save_csv(margin, os.path.join(out, "03_margin_daily.csv"))
    p = report.plot_margin(margin, os.path.join(out, "03_margin.png"))
    if p:
        png_map["03_margin"] = p

    gv = bundle.get("growth_value", pd.DataFrame())
    _save_csv(gv, os.path.join(out, "04_growth_value.csv"))
    p = report.plot_growth_value(gv, os.path.join(out, "04_growth_value.png"))
    if p:
        png_map["04_growth_value"] = p

    hl_m = bundle.get("high_low", pd.DataFrame())
    _save_csv(hl_m, os.path.join(out, "05_high_low.csv"))
    _save_csv(bundle.get("unlock_daily", pd.DataFrame()), os.path.join(out, "06_unlock_daily.csv"))
    p = report.plot_high_low(hl_m, os.path.join(out, "05_high_low.png"))
    if p:
        png_map["05_high_low"] = p

    report.write_pdf(list(png_map.values()), os.path.join(out, "五项指标复现_本地.pdf"))

    long_pdf = os.path.join(out, "大周期五项指标_长期跟踪.pdf")
    latest = os.path.join(dp_cycle_store_dir(), "大周期五项指标_长期跟踪.pdf")
    research_copy = os.path.join(project_root(), "research", "DP", "大周期五项指标_长期跟踪.pdf")

    # long_term_pdf 期望 indicators 为各 ok 字典
    from data_source.dp_cycle.diagnosis import _etf_coverage

    etf_coverage = (((diagnosis or {}).get("data_availability") or {}).get("national_team") or {}).get("coverage")
    if not etf_coverage:
        etf_coverage = _etf_coverage(etf_hist)
    pdf_meta = {
        "indicators": {
            "01_main_force": {**(bundle.get("meta") or {}).get("01_main_force", {}), "today_净额_亿元": None},
            "02_national_team": {
                "ok": not etf_hist.empty,
                "partial": not etf_coverage or etf_coverage.get("complete") is not True,
                "coverage": etf_coverage,
            },
            "03_margin": {
                "ok": not margin.empty,
                "last_date": str(margin["date"].iloc[-1])[:10] if not margin.empty else None,
                "last_融资余额_亿元": float(margin["融资余额_亿元"].iloc[-1]) if not margin.empty else None,
                "lookback_years": 5,
            },
            "04_growth_value": {
                "ok": not gv.empty,
                "last_date": str(gv["date"].iloc[-1])[:10] if not gv.empty else None,
                "last_偏离度": float(gv["偏离度"].iloc[-1]) if not gv.empty and "偏离度" in gv.columns else None,
            },
            "05_high_low": {
                "ok": not hl_m.empty,
                "last_date": str(hl_m["date"].iloc[-1])[:10] if not hl_m.empty else None,
                "last_新高": int(hl_m["new_high"].iloc[-1]) if not hl_m.empty else None,
                "last_新低": int(hl_m["new_low"].iloc[-1]) if not hl_m.empty else None,
            },
        }
    }
    if not mf_agg["daily"].empty and "净额_亿元" in mf_agg["daily"].columns:
        pdf_meta["indicators"]["01_main_force"]["today_净额_亿元"] = float(
            mf_agg["daily"]["净额_亿元"].iloc[-1]
        )

    long_path = build_long_term_pdf(
        out_pdf=long_pdf,
        meta=pdf_meta,
        pngs=png_map,
        tables=bundle,
        diagnosis=diagnosis,
        latest_copy=latest if publish_latest else None,
    )
    import shutil

    if publish_latest:
        os.makedirs(os.path.dirname(research_copy), exist_ok=True)
        # Preserve the reference PDF shipped with the repository.
        if not os.path.exists(research_copy):
            shutil.copy2(long_path, research_copy)

    meta["pngs"] = png_map
    meta["pdf"] = long_path
    meta["pdf_latest"] = latest if publish_latest else None
    meta["pdf_research"] = research_copy if publish_latest else None
    report.write_meta(os.path.join(out, "scan_meta.txt"), meta)
    with open(os.path.join(out, "scan_meta_summary.txt"), "w", encoding="utf-8") as f:
        f.write(f"dp_cycle pipeline\nout={out}\nhistory_start={HISTORY_START_STR}\npdf={long_path}\n")
    return meta
