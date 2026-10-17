# -*- coding: utf-8 -*-
"""01 主力资金（东财大盘资金流日频）→ 可聚合周/月。

口径：东方财富「主力净流入-净额」（大单+超大单），单位亿元。
≠ 股东身份；≠ 同花顺行业截面加总。作者月−4000 / 周−2000 仅对照。
"""

from __future__ import annotations

import json
import time
from typing import Any

import pandas as pd
import requests

AUTHOR_MONTH_LINE = -4000.0
AUTHOR_WEEK_LINE = -2000.0

_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/zjlx/dpzjlx.html",
}


def _params() -> dict[str, Any]:
    return {
        "lmt": "0",
        "klt": "101",
        "secid": "1.000001",
        "secid2": "0.399001",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "_": str(int(time.time() * 1000)),
    }


def _parse_klines(klines: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for line in klines or []:
        p = str(line).split(",")
        if len(p) < 6:
            continue
        try:
            rows.append(
                {
                    "date": p[0],
                    "净额_亿元": float(p[1]) / 1e8,
                    "小单_亿元": float(p[2]) / 1e8,
                    "中单_亿元": float(p[3]) / 1e8,
                    "大单_亿元": float(p[4]) / 1e8,
                    "超大单_亿元": float(p[5]) / 1e8,
                }
            )
        except (TypeError, ValueError):
            continue
    if not rows:
        return pd.DataFrame(
            columns=["date", "净额_亿元", "小单_亿元", "中单_亿元", "大单_亿元", "超大单_亿元"]
        )
    out = pd.DataFrame(rows)
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    out = out.dropna(subset=["date", "净额_亿元"]).sort_values("date").drop_duplicates("date")
    return out.reset_index(drop=True)


def union_main_force_frames(*frames: pd.DataFrame) -> pd.DataFrame:
    """按日期合并多份主力日频。后出现的帧覆盖同一天，更早的日期保留。"""
    cols = ["date", "净额_亿元", "流入_亿元", "流出_亿元", "行业数", "source"]
    parts: list[pd.DataFrame] = []
    for frame in frames:
        if frame is None or getattr(frame, "empty", True):
            continue
        x = frame.copy()
        if "净额_亿元" not in x.columns and "net_yi" in x.columns:
            x = x.rename(columns={"net_yi": "净额_亿元"})
        x["date"] = pd.to_datetime(x.get("date"), errors="coerce").dt.normalize()
        for col in cols:
            if col not in x.columns:
                x[col] = pd.NA
        parts.append(x[cols])
    if not parts:
        return pd.DataFrame(columns=cols)
    merged = pd.concat(parts, ignore_index=True)
    merged["净额_亿元"] = pd.to_numeric(merged["净额_亿元"], errors="coerce")
    merged = merged.dropna(subset=["date", "净额_亿元"])
    # sort 稳定，同日保留后一帧
    return (
        merged.sort_values("date", kind="mergesort")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )


def persist_em_klines(klines: list[str], path: str) -> int:
    """把东财 kline 文本按日期并进本地 JSON。新响应只覆盖同一天，不缩短历史。"""
    import os

    old: list[str] = []
    if os.path.isfile(path):
        try:
            raw = json.loads(open(path, encoding="utf-8").read())
            old = list((raw.get("data") or {}).get("klines") or raw.get("klines") or [])
        except Exception:
            old = []
    by_date: dict[str, str] = {}
    for line in list(old) + list(klines or []):
        day = str(line).split(",", 1)[0].strip()
        if day:
            by_date[day] = str(line)
    merged = [by_date[k] for k in sorted(by_date)]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"data": {"klines": merged}}, fh, ensure_ascii=False)
    return len(merged)


def fetch_em_market_fund_flow(*, retries: int = 8) -> tuple[pd.DataFrame, dict[str, Any]]:
    """直连东财大盘资金流日 K（绕开偶发 akshare 断连）。"""
    meta: dict[str, Any] = {
        "source": "eastmoney.push2his.fflow.daykline",
        "ok": False,
        "error": None,
        "author_month_line": AUTHOR_MONTH_LINE,
        "author_week_line": AUTHOR_WEEK_LINE,
        "note": "主力=大单+超大单净流入；≠股东身份；历史窗口以接口返回为准",
    }
    last: Exception | None = None
    session = requests.Session()
    session.trust_env = False
    for i in range(max(1, retries)):
        try:
            if i:
                time.sleep(1.2 * i)
            r = session.get(_URL, params=_params(), headers=_HEADERS, timeout=45)
            r.raise_for_status()
            data = r.json().get("data") or {}
            klines = data.get("klines") or []
            df = _parse_klines(klines)
            if df.empty:
                raise RuntimeError("empty klines")
            meta["ok"] = True
            meta["rows"] = int(len(df))
            meta["first_date"] = str(df["date"].iloc[0].date())
            meta["last_date"] = str(df["date"].iloc[-1].date())
            meta["last_净额_亿元"] = float(df["净额_亿元"].iloc[-1])
            try:
                import os

                from output_paths import dp_cycle_store_dir

                cache_path = os.path.join(dp_cycle_store_dir(), "_em_fflow_raw.json")
                meta["cache_rows"] = persist_em_klines(klines, cache_path)
            except Exception as exc:  # noqa: BLE001
                meta["cache_error"] = f"{type(exc).__name__}: {exc}"
            return df, meta
        except Exception as exc:  # noqa: BLE001
            last = exc
            meta["error"] = f"{type(exc).__name__}: {exc}"
    return pd.DataFrame(), meta


def load_em_fund_flow_cache(path: str) -> pd.DataFrame:
    """从已落盘的东财 JSON/CSV 恢复（网络不稳时用）。"""
    import os

    if not os.path.isfile(path):
        return pd.DataFrame()
    if path.lower().endswith(".json"):
        raw = json.loads(open(path, encoding="utf-8").read())
        klines = (raw.get("data") or {}).get("klines") or raw.get("klines") or []
        return _parse_klines(klines)
    df = pd.read_csv(path)
    if "date" not in df.columns:
        return pd.DataFrame()
    col = "净额_亿元" if "净额_亿元" in df.columns else (
        "主力净流入_亿元" if "主力净流入_亿元" in df.columns else None
    )
    if col is None:
        return pd.DataFrame()
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df["date"], errors="coerce").dt.normalize(),
            "净额_亿元": pd.to_numeric(df[col], errors="coerce"),
        }
    )
    return out.dropna(subset=["date", "净额_亿元"]).sort_values("date").reset_index(drop=True)


def aggregate_main_force(daily: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if daily is None or daily.empty:
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
