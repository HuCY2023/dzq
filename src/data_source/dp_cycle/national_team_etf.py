# -*- coding: utf-8 -*-
"""02 国家队代理：宽基 ETF 份额/规模变动（≠ 汇金/证金身份）。"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from data_source.dp_cycle._http import call_with_retry

# 与 research/touyan/gjd资金跟踪 一致
CORE_CSI300 = ("510300", "510310", "510330", "159919")
SSE_CORE_CSI300 = tuple(code for code in CORE_CSI300 if code.startswith("51"))
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


def _load_history(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        return pd.DataFrame(columns=["date", "code", "name", "基金份额", "份额_亿份"])
    hist = pd.read_csv(path, dtype={"code": str})
    hist["date"] = pd.to_datetime(hist["date"], errors="coerce").dt.normalize()
    hist["code"] = hist["code"].astype(str).str.zfill(6)
    return hist


def _merge_history(*frames: pd.DataFrame) -> pd.DataFrame:
    existing = [frame for frame in frames if isinstance(frame, pd.DataFrame) and not frame.empty]
    if not existing:
        return pd.DataFrame(columns=["date", "code", "name", "基金份额", "份额_亿份"])
    hist = pd.concat(existing, ignore_index=True)
    hist["date"] = pd.to_datetime(hist["date"], errors="coerce").dt.normalize()
    hist["code"] = hist["code"].astype(str).str.zfill(6)
    shares = pd.to_numeric(hist["基金份额"], errors="coerce")
    shares_yi = pd.to_numeric(hist["份额_亿份"], errors="coerce")
    hist["基金份额"] = shares.combine_first(shares_yi * 1e8)
    hist["份额_亿份"] = shares_yi.combine_first(shares / 1e8)
    return hist.dropna(subset=["date"]).groupby(["date", "code"], as_index=False).last().sort_values(["date", "code"])


def fetch_sse_etf_scale(as_of: date | None = None, *, exact_date: bool = False) -> tuple[pd.DataFrame, dict[str, Any]]:
    """上交所 ETF 基金份额（份）；月末回填可限定只查询给定日期。"""
    import akshare as ak

    d = as_of or date.today()
    meta: dict[str, Any] = {"source": "akshare.fund_etf_scale_sse", "ok": False, "error": None, "date": str(d)}
    # 日常快照可向前回退；已核定月末只查精确日期。
    last_err: Exception | None = None
    for i in range(1 if exact_date else 8):
        day = d - timedelta(days=i)
        try:
            if exact_date:
                df = call_with_retry(ak.fund_etf_scale_sse, date=day.strftime("%Y%m%d"), retries=1)
            else:
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
    hist = _load_history(path)

    rows: list[dict[str, Any]] = []
    if scale_meta.get("ok") and not scale.empty:
        snap_date = pd.to_datetime(scale_meta.get("date"), errors="coerce")
        if pd.notna(snap_date) and "统计日期" in scale.columns:
            scale = scale.loc[pd.to_datetime(scale["统计日期"], errors="coerce").dt.normalize() == snap_date.normalize()]
        else:
            scale = scale.iloc[0:0]
        meta["observation_date"] = str(snap_date.date()) if pd.notna(snap_date) else None
        for code in CORE_CSI300:
            hit = scale.loc[scale["基金代码"] == code]
            if hit.empty:
                continue
            shares = pd.to_numeric(hit["基金份额"].iloc[0], errors="coerce")
            if pd.isna(shares):
                continue
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
            hist = _merge_history(hist, add)
            hist.to_csv(path, index=False, encoding="utf-8-sig")
            meta["wrote_rows"] = len(rows)
            observed = {row["code"] for row in rows}
            meta["missing_codes"] = sorted(set(CORE_CSI300) - observed)
            meta["partial"] = bool(meta["missing_codes"])
            meta["ok"] = not meta["partial"]
        else:
            meta["error"] = meta.get("error") or "core codes not in SSE scale table"
            if not hist.empty:
                meta["partial"] = True
                meta["from_cache"] = True
    elif not hist.empty:
        meta["partial"] = True
        meta["from_cache"] = True
        meta["error"] = meta.get("error") or "scale fetch failed; using store only"

    return hist.reset_index(drop=True), meta


def backfill_core_etf_month_ends(*, months: int = 24, as_of: date | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """逐只核对各月最后交易日，SSE 源不能补齐的深市基金明确列缺。"""
    from data_source.dp_cycle.trade_calendar import load_trade_calendar

    meta: dict[str, Any] = {
        "source": "sse_etf_scale_month_ends",
        "ok": False,
        "error": None,
        "tried": 0,
        "hit": 0,
        "partial": False,
        "missing_targets": [],
        "unknown_calendar_months": [],
        "source_failures": [],
    }
    today = as_of or date.today()
    targets: list[date] = []
    cursor = date(today.year, today.month, 1)
    for _ in range(max(1, months)):
        month_end = cursor - timedelta(days=1)
        targets.append(month_end)
        cursor = date(month_end.year, month_end.month, 1)

    path = os.path.join(_store_dir(), "core_shares_daily.csv")
    hist = _load_history(path)
    calendar, calendar_meta = load_trade_calendar(refresh=False)
    meta["calendar_source"] = calendar_meta.get("origin") or "local calendar cache (origin unrecorded)"
    meta["calendar_coverage"] = {"start": calendar_meta.get("start_date"), "end": calendar_meta.get("end_date")}
    dates = pd.to_datetime(calendar["date"], errors="coerce").dropna().dt.normalize() if (
        calendar_meta.get("ok") and isinstance(calendar, pd.DataFrame) and "date" in calendar.columns
    ) else pd.Series(dtype="datetime64[ns]")
    coverage_start = pd.to_datetime(calendar_meta.get("start_date"), errors="coerce")
    coverage_end = pd.to_datetime(calendar_meta.get("end_date"), errors="coerce")

    added = 0
    for d in targets:
        key = f"{d.year:04d}-{d.month:02d}"
        month_start, month_end = pd.Timestamp(d.replace(day=1)), pd.Timestamp(d)
        if pd.isna(coverage_start) or pd.isna(coverage_end) or coverage_start > month_start or coverage_end < month_end:
            meta["unknown_calendar_months"].append(key)
            continue
        month_dates = dates.loc[(dates >= month_start) & (dates <= month_end)]
        if month_dates.empty:
            meta["unknown_calendar_months"].append(key)
            continue
        expected = pd.Timestamp(month_dates.max()).normalize()

        def observed_codes() -> set[str]:
            if hist.empty:
                return set()
            valid = hist.loc[(hist["date"] == expected) & pd.to_numeric(hist["份额_亿份"], errors="coerce").notna()]
            return set(valid["code"].astype(str).str.zfill(6))

        existing = observed_codes()
        if not set(SSE_CORE_CSI300).issubset(existing):
            meta["tried"] += 1
            scale, sm = fetch_sse_etf_scale(expected.date(), exact_date=True)
            actual = pd.to_datetime(sm.get("date"), errors="coerce")
            rows: list[dict[str, Any]] = []
            source_error = sm.get("error")
            returned_row_dates: list[str] = []
            if not sm.get("ok") or scale.empty:
                source_error = source_error or "来源请求失败或返回空数据"
            elif pd.isna(actual) or actual.normalize() != expected:
                source_error = "来源返回日期不等于最后交易日"
            elif "统计日期" not in scale:
                source_error = "来源缺少统计日期"
            if sm.get("ok") and not scale.empty and pd.notna(actual) and actual.normalize() == expected and "统计日期" in scale:
                scale = scale.copy()
                scale["统计日期"] = pd.to_datetime(scale["统计日期"], errors="coerce").dt.normalize()
                returned_row_dates = sorted(scale["统计日期"].dropna().dt.strftime("%Y-%m-%d").unique().tolist())
                scale["基金代码"] = scale["基金代码"].astype(str).str.zfill(6)
                for code in SSE_CORE_CSI300:
                    hit = scale.loc[(scale["基金代码"] == code) & (scale["统计日期"] == expected)]
                    if hit.empty:
                        continue
                    shares = pd.to_numeric(hit["基金份额"].iloc[0], errors="coerce")
                    if pd.notna(shares):
                        rows.append({"date": expected, "code": code, "name": CORE_LABELS.get(code, code),
                                     "基金份额": float(shares), "份额_亿份": float(shares) / 1e8})
            if rows:
                hist = _merge_history(hist, pd.DataFrame(rows))
                added += 1
                meta["hit"] += 1
            missing_sse = sorted(set(SSE_CORE_CSI300) - observed_codes())
            if missing_sse:
                meta["source_failures"].append({
                    "month": key,
                    "expected_date": str(expected.date()),
                    "returned_date": str(actual.date()) if pd.notna(actual) else None,
                    "returned_row_dates": returned_row_dates,
                    "missing_codes": missing_sse,
                    "error": source_error or "来源未返回完整的月末基金份额和统计日期",
                })
        missing = sorted(set(CORE_CSI300) - observed_codes())
        if missing:
            meta["missing_targets"].append({"month": key, "last_trading_day": str(expected.date()), "missing_codes": missing})

    if added:
        hist.to_csv(path, index=False, encoding="utf-8-sig")
    meta["partial"] = bool(meta["missing_targets"] or meta["unknown_calendar_months"])
    meta["ok"] = not meta["partial"]
    meta["wrote_months"] = added
    meta["rows"] = int(len(hist))
    if meta["unknown_calendar_months"]:
        meta["error"] = "交易日历不覆盖部分月份，无法确认最后交易日"
    return hist.reset_index(drop=True), meta


def adjust_share_events(hist: pd.DataFrame) -> pd.DataFrame:
    """保留原始份额，新增按已发生份额事件折算的亿份列。"""
    h = hist.copy()
    if hist.empty:
        return h
    h["date"] = pd.to_datetime(h["date"])
    h["份额_调整后_亿份"] = pd.to_numeric(h["份额_亿份"], errors="coerce")
    if "code" in h.columns:
        event_path = Path(__file__).resolve().parents[3] / "research" / "DP" / "data" / "etf_share_events.csv"
        events = pd.read_csv(event_path, dtype={"code": str}, parse_dates=["event_date"])
        codes = h["code"].astype(str).str.zfill(6)
        for event in events.itertuples(index=False):
            if event.event_date <= h["date"].max():
                mask = (codes == event.code) & (h["date"] < event.event_date)
                if mask.any():
                    h["份额_调整后_亿份"] = h["份额_调整后_亿份"].astype(float)
                    h.loc[mask, "份额_调整后_亿份"] *= event.ratio
    return h


def monthly_share_change(hist: pd.DataFrame) -> pd.DataFrame:
    """月末份额合计与月变动（亿份）；已发生的份额事件按基金折算。"""
    if hist.empty:
        return pd.DataFrame()
    h = adjust_share_events(hist)
    # 每日核心合计
    daily = h.groupby("date", as_index=False)["份额_调整后_亿份"].sum(min_count=1).rename(
        columns={"份额_调整后_亿份": "份额_亿份"}
    )
    daily = daily.sort_values("date")
    daily["month"] = daily["date"].dt.to_period("M")
    month_end = daily.groupby("month", as_index=False).tail(1).copy()
    month_end["月变动_亿份"] = month_end["份额_亿份"].diff()
    month_number = month_end["month"].dt.year * 12 + month_end["month"].dt.month
    comparable = month_number.diff().eq(1)
    if "code" in h.columns:
        valid = h.dropna(subset=["code", "份额_调整后_亿份"])
        members = valid.groupby("date")["code"].agg(lambda values: tuple(sorted(set(values.astype(str).str.zfill(6)))))
        snapshots = month_end["date"].map(members)
        comparable &= snapshots.notna() & snapshots.eq(snapshots.shift())
    month_end.loc[~comparable, "月变动_亿份"] = float("nan")
    month_end["month_start"] = month_end["month"].dt.to_timestamp()
    return month_end.reset_index(drop=True)
