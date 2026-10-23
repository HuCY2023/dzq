# -*- coding: utf-8 -*-
"""01 主力资金：同花顺行业资金流截面加总 + 本地日增量库。"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import pandas as pd

from data_source.dp_cycle._http import call_with_retry

AUTHOR_MONTH_LINE = -4000.0  # 亿元，仅对照
AUTHOR_WEEK_LINE = -2000.0


def _store_dir() -> str:
    from output_paths import dp_cycle_store_dir

    p = os.path.join(dp_cycle_store_dir(), "main_force_ths")
    os.makedirs(p, exist_ok=True)
    return p


def _daily_path() -> str:
    return os.path.join(_store_dir(), "daily_net.csv")


def fetch_ths_industry_snapshot(symbol: str = "即时") -> tuple[pd.DataFrame, dict[str, Any]]:
    """同花顺行业资金流截面（净额单位：亿元）。"""
    import akshare as ak

    meta: dict[str, Any] = {
        "source": f"akshare.stock_fund_flow_industry(symbol={symbol!r})",
        "ok": False,
        "error": None,
    }
    try:
        df = call_with_retry(ak.stock_fund_flow_industry, symbol=symbol)
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return pd.DataFrame(), meta
    meta["ok"] = True
    meta["rows"] = int(len(df))
    meta["sum_净额_亿元"] = float(pd.to_numeric(df["净额"], errors="coerce").sum())
    return df, meta


def upsert_today_from_ths(*, as_of: date | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """拉取当日截面加总并写入增量库；返回全历史日表。"""
    as_of = as_of or date.today()
    snap, snap_meta = fetch_ths_industry_snapshot("即时")
    meta: dict[str, Any] = {
        "source": "ths_industry_sum+local_csv",
        "ok": False,
        "error": snap_meta.get("error"),
        "snapshot": snap_meta,
        "author_month_line": AUTHOR_MONTH_LINE,
        "author_week_line": AUTHOR_WEEK_LINE,
        "note": "行业净额加总口径；历史需自积累；大单≠股东身份",
    }
    path = _daily_path()
    if os.path.isfile(path):
        hist = pd.read_csv(path)
        hist["date"] = pd.to_datetime(hist["date"]).dt.normalize()
    else:
        hist = pd.DataFrame(columns=["date", "净额_亿元", "流入_亿元", "流出_亿元", "行业数"])

    if snap_meta.get("ok") and not snap.empty:
        row = {
            "date": pd.Timestamp(as_of),
            "净额_亿元": float(pd.to_numeric(snap["净额"], errors="coerce").sum()),
            "流入_亿元": float(pd.to_numeric(snap["流入资金"], errors="coerce").sum()),
            "流出_亿元": float(pd.to_numeric(snap["流出资金"], errors="coerce").sum()),
            "行业数": int(len(snap)),
        }
        hist = hist[hist["date"] != pd.Timestamp(as_of)]
        # ── 性能优化：numpy append 替代 pd.concat 单行追加 ──
        import numpy as np
        new_row = pd.DataFrame([row])
        if hist.empty:
            hist = new_row
        else:
            hist = pd.concat([hist, new_row], ignore_index=True)
        hist = hist.sort_values("date")
        hist.to_csv(path, index=False, encoding="utf-8-sig")
        meta["ok"] = True
        meta["wrote_date"] = str(as_of)
        meta["today_净额_亿元"] = row["净额_亿元"]
    elif not hist.empty:
        # 无新截面时仍返回历史
        meta["ok"] = True
        meta["error"] = meta.get("error") or "snapshot failed; using store only"
    else:
        meta["ok"] = False
        if not meta.get("error"):
            meta["error"] = "no snapshot and empty store"

    return hist.reset_index(drop=True), meta


def aggregate_main_force(daily: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """日 → 周/月聚合。"""
    if daily.empty:
        empty = pd.DataFrame()
        return {"daily": empty, "weekly": empty, "monthly": empty}
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date")
    d["作者周警戒线"] = AUTHOR_WEEK_LINE
    d["作者月基准线"] = AUTHOR_MONTH_LINE

    w = d.set_index("date")[["净额_亿元"]].resample("W-FRI").sum(min_count=1).dropna().reset_index()
    w["作者周警戒线"] = AUTHOR_WEEK_LINE

    m = d.set_index("date")[["净额_亿元"]].resample("ME").sum(min_count=1).dropna().reset_index()
    m["作者月基准线"] = AUTHOR_MONTH_LINE
    return {"daily": d.reset_index(drop=True), "weekly": w, "monthly": m}
