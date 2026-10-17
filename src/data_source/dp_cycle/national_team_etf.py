# -*- coding: utf-8 -*-
"""02 国家队代理：宽基 ETF 份额/规模变动（≠ 汇金/证金身份）。"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import pandas as pd

from data_source.dp_cycle._http import call_with_retry

# 与 research/touyan/gjd资金跟踪 一致
CORE_CSI300 = ("510300", "510310", "510330", "159919")
CORE_LABELS = {
    "510300": "华泰柏瑞沪深300",
    "510310": "易方达沪深300",
    "510330": "华夏沪深300",
    "159919": "嘉实沪深300",
}


def _store_dir() -> str:
    from output_paths import dp_cycle_store_dir

    p = os.path.join(dp_cycle_store_dir(), "national_team_etf")
    os.makedirs(p, exist_ok=True)
    return p


def fetch_sse_etf_scale(as_of: date | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """上交所 ETF 基金份额（份）。"""
    import akshare as ak

    d = as_of or date.today()
    meta: dict[str, Any] = {"source": "akshare.fund_etf_scale_sse", "ok": False, "error": None, "date": str(d)}
    # 非交易日向前试几天
    last_err: Exception | None = None
    for i in range(8):
        day = d - timedelta(days=i)
        try:
            df = call_with_retry(ak.fund_etf_scale_sse, date=day.strftime("%Y%m%d"))
            if df is None or df.empty:
                continue
            out = df.copy()
            out["统计日期"] = pd.to_datetime(out["统计日期"], errors="coerce").dt.normalize()
            out["基金份额"] = pd.to_numeric(out["基金份额"], errors="coerce")
            out["基金代码"] = out["基金代码"].astype(str).str.zfill(6)
            meta["ok"] = True
            meta["date"] = str(day)
            meta["rows"] = int(len(out))
            return out, meta
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    meta["error"] = f"{type(last_err).__name__}: {last_err}" if last_err else "empty"
    return pd.DataFrame(), meta


def snapshot_core_etfs(as_of: date | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """抽取核心宽基份额并写入增量库。"""
    scale, scale_meta = fetch_sse_etf_scale(as_of)
    meta: dict[str, Any] = {
        "source": "sse_etf_scale+local_csv",
        "ok": False,
        "error": scale_meta.get("error"),
        "scale": scale_meta,
        "note": "份额变动为申赎代理，不能识别汇金/证金身份；159919 在深交所，SSE 表可能缺失",
        "core": list(CORE_CSI300),
    }
    path = os.path.join(_store_dir(), "core_shares_daily.csv")
    if os.path.isfile(path):
        hist = pd.read_csv(path)
        hist["date"] = pd.to_datetime(hist["date"]).dt.normalize()
    else:
        hist = pd.DataFrame(columns=["date", "code", "name", "基金份额", "份额_亿份"])

    rows: list[dict[str, Any]] = []
    if scale_meta.get("ok") and not scale.empty:
        snap_date = pd.to_datetime(scale_meta["date"])
        for code in CORE_CSI300:
            hit = scale.loc[scale["基金代码"] == code]
            if hit.empty:
                continue
            shares = float(hit["基金份额"].iloc[0])
            rows.append(
                {
                    "date": snap_date,
                    "code": code,
                    "name": CORE_LABELS.get(code, code),
                    "基金份额": shares,
                    "份额_亿份": shares / 1e8,
                }
            )
        if rows:
            add = pd.DataFrame(rows)
            hist = hist[~((hist["date"] == snap_date) & (hist["code"].isin(add["code"])))]
            hist = pd.concat([hist, add], ignore_index=True)
            hist = hist.sort_values(["date", "code"])
            hist.to_csv(path, index=False, encoding="utf-8-sig")
            meta["ok"] = True
            meta["wrote_rows"] = len(rows)
        else:
            meta["error"] = meta.get("error") or "core codes not in SSE scale table"
            if not hist.empty:
                meta["ok"] = True
    elif not hist.empty:
        meta["ok"] = True
        meta["error"] = meta.get("error") or "scale fetch failed; using store only"

    return hist.reset_index(drop=True), meta


def backfill_core_etf_month_ends(*, months: int = 24) -> tuple[pd.DataFrame, dict[str, Any]]:
    """按月末回填上交所核心 ETF 份额（开放源慢爬，用于拉长 02 历史）。"""
    meta: dict[str, Any] = {
        "source": "sse_etf_scale_month_ends",
        "ok": False,
        "error": None,
        "tried": 0,
        "hit": 0,
    }
    today = date.today()
    # 近 N 个月每个月月末附近（向前找最多 6 个交易日）
    targets: list[date] = []
    cursor = date(today.year, today.month, 1)
    for _ in range(max(1, months)):
        # 上月最后一天
        month_end = cursor - timedelta(days=1)
        targets.append(month_end)
        cursor = date(month_end.year, month_end.month, 1)

    path = os.path.join(_store_dir(), "core_shares_daily.csv")
    if os.path.isfile(path):
        hist = pd.read_csv(path)
        hist["date"] = pd.to_datetime(hist["date"]).dt.normalize()
    else:
        hist = pd.DataFrame(columns=["date", "code", "name", "基金份额", "份额_亿份"])

    existing = set()
    if not hist.empty:
        existing = set(pd.to_datetime(hist["date"]).dt.strftime("%Y-%m").unique())

    added = 0
    for d in targets:
        key = f"{d.year:04d}-{d.month:02d}"
        if key in existing:
            continue
        meta["tried"] += 1
        scale, sm = fetch_sse_etf_scale(d)
        if not sm.get("ok") or scale.empty:
            continue
        snap_date = pd.to_datetime(sm["date"])
        rows: list[dict[str, Any]] = []
        for code in CORE_CSI300:
            hit = scale.loc[scale["基金代码"] == code]
            if hit.empty:
                continue
            shares = float(hit["基金份额"].iloc[0])
            rows.append(
                {
                    "date": snap_date,
                    "code": code,
                    "name": CORE_LABELS.get(code, code),
                    "基金份额": shares,
                    "份额_亿份": shares / 1e8,
                }
            )
        if not rows:
            continue
        add = pd.DataFrame(rows)
        hist = hist[~((hist["date"] == snap_date) & (hist["code"].isin(add["code"])))]
        hist = pd.concat([hist, add], ignore_index=True)
        existing.add(key)
        added += 1
        meta["hit"] += 1

    if not hist.empty:
        hist = hist.sort_values(["date", "code"])
        hist.to_csv(path, index=False, encoding="utf-8-sig")
        meta["ok"] = True
        meta["wrote_months"] = added
        meta["rows"] = int(len(hist))
    return hist.reset_index(drop=True), meta


def monthly_share_change(hist: pd.DataFrame) -> pd.DataFrame:
    """月末份额合计与月变动（亿份）。"""
    if hist.empty:
        return pd.DataFrame()
    h = hist.copy()
    h["date"] = pd.to_datetime(h["date"])
    # 每日核心合计
    daily = h.groupby("date", as_index=False)["份额_亿份"].sum()
    daily = daily.sort_values("date")
    daily["month"] = daily["date"].dt.to_period("M")
    month_end = daily.groupby("month", as_index=False).tail(1).copy()
    month_end["月变动_亿份"] = month_end["份额_亿份"].diff()
    month_end["month_start"] = month_end["month"].dt.to_timestamp()
    return month_end.reset_index(drop=True)
