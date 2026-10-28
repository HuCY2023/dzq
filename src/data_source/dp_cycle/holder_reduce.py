# -*- coding: utf-8 -*-
"""重要股东减持：东财 RPT_SHARE_HOLDER_INCREASE，按变动截止日。

CHANGE_NUM 为万股。金额（亿元）= |万股| × 收盘价 / 10000。
"""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
import requests

from data_source.dp_cycle.margin import _margin_local_timestamp, _margin_local_timestamps

_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/executive/gdzjc.html",
}


def fetch_holder_reduces(*, start: str = "2015-01-01", page_size: int = 500) -> tuple[pd.DataFrame, dict[str, Any]]:
    meta: dict[str, Any] = {
        "source": "eastmoney.RPT_SHARE_HOLDER_INCREASE",
        "ok": False,
        "error": None,
        "start": start,
    }
    session = requests.Session()
    session.trust_env = False
    params = {
        "reportName": "RPT_SHARE_HOLDER_INCREASE",
        "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,HOLDER_NAME,END_DATE,NOTICE_DATE,CHANGE_NUM,CLOSE_PRICE,DIRECTION",
        "pageSize": str(page_size),
        "pageNumber": "1",
        "sortColumns": "END_DATE",
        "sortTypes": "1",
        "source": "WEB",
        "client": "WEB",
        "filter": f'(DIRECTION="减持")(END_DATE>=\'{start}\')',
    }
    rows: list[dict[str, Any]] = []
    try:
        first = session.get(_URL, params=params, headers=_HEADERS, timeout=40)
        first.raise_for_status()
        body = first.json()
        result = body.get("result") or {}
        pages = int(result.get("pages") or 1)
        meta["count"] = result.get("count")
        meta["pages"] = pages
        chunk = result.get("data") or []
        rows.extend(chunk)
        for page in range(2, pages + 1):
            if page % 20 == 0:
                time.sleep(0.4)
            params["pageNumber"] = str(page)
            r = session.get(_URL, params=params, headers=_HEADERS, timeout=40)
            r.raise_for_status()
            rows.extend(((r.json().get("result") or {}).get("data")) or [])
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        if not rows:
            return pd.DataFrame(), meta
        meta["partial"] = True

    if not rows:
        meta["error"] = "empty"
        return pd.DataFrame(), meta
    expected = meta.get("count")
    if expected is not None and len(rows) < int(expected):
        meta["partial"] = True
        meta["error"] = meta.get("error") or f"分页返回 {len(rows)}/{expected} 条"
    raw = pd.DataFrame(rows)
    out = pd.DataFrame(
        {
            "code": raw.get("SECURITY_CODE", pd.Series(dtype=str)).astype(str),
            "holder": raw.get("HOLDER_NAME", pd.Series(dtype=str)).fillna("").astype(str),
            "end_date": pd.to_datetime(raw.get("END_DATE"), errors="coerce"),
            "notice_date": pd.to_datetime(raw.get("NOTICE_DATE"), errors="coerce"),
            "change_wan": pd.to_numeric(raw.get("CHANGE_NUM"), errors="coerce"),
            "close_price": pd.to_numeric(raw.get("CLOSE_PRICE"), errors="coerce"),
        }
    )
    out["amount_yi"] = out["change_wan"].abs() * out["close_price"] / 10000.0
    meta["raw_rows"] = int(len(out))
    out = out.dropna(subset=["code", "end_date", "change_wan"])
    out["notice_known"] = out["notice_date"].notna()
    meta["notice_unknown"] = int((~out["notice_known"]).sum())
    out["notice_date"] = out["notice_date"].fillna(out["end_date"])
    # 同一键冲突时保留真正有公告日期的记录，缺公告日期的占位键不能抹掉它。
    out = out.sort_values("notice_known").drop_duplicates(
        ["code", "holder", "end_date", "notice_date"], keep="last"
    ).sort_index()
    meta["valid_amount_rows"] = int(out["amount_yi"].notna().sum())
    if len(out) < meta["raw_rows"] or meta["valid_amount_rows"] < len(out) or meta["notice_unknown"]:
        meta["partial"] = True
        meta["error"] = meta.get("error") or "分页重复或记录字段不完整，无法确认全部金额及公告日"
    meta["ok"] = not meta.get("partial", False)
    meta["rows"] = int(len(out))
    return out.reset_index(drop=True), meta


def annotate_holder_notices(frame: pd.DataFrame) -> pd.DataFrame:
    """标注公告日是否可证；老库的同日占位值无法与真实同日公告区分。"""
    out = frame.copy()
    if "notice_known" in out.columns:
        known = out["notice_known"].astype("boolean")
    else:
        known = pd.Series(pd.NA, index=out.index, dtype="boolean")
    date_col = "end_date" if "end_date" in out.columns else "date"
    if date_col in out.columns and "notice_date" in out.columns:
        end = _margin_local_timestamps(out[date_col]).dt.normalize()
        notice = _margin_local_timestamps(out["notice_date"]).dt.normalize()
        # 老版本仅在公告日缺失时回填 end_date；不相同的日期不是该回填所得。
        inferred = end.notna() & notice.notna() & end.ne(notice)
        known = known.mask(known.isna() & inferred, True)
    out["notice_known"] = known
    return out


def visible_holder_reductions(frame: pd.DataFrame | None, *, as_of: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
    """只保留分析日之前已发生且公告日已过的记录；无公告时刻时按次日可用。

    旧库的同日公告可能是原程序的占位回填，无法确认的记录保留为缺口，
    不参与可见记录。变动截止日也是日期而非时刻，按次日已完成处理。
    时间统一按台北本地时间比较；带时区的输入先转换，无时区输入视为本地时间。
    """
    if not isinstance(frame, pd.DataFrame):
        frame = pd.DataFrame()
    out = annotate_holder_notices(frame)
    cutoff = _margin_local_timestamp(as_of)
    if out.empty:
        return out, {"status": "gap", "input_rows": 0, "visible_rows": 0,
                     "notice_unknown": 0, "not_yet_available": 0, "future_event": 0}
    date_col = "end_date" if "end_date" in out.columns else "date"
    end = _margin_local_timestamps(out[date_col]).dt.normalize()
    notice = _margin_local_timestamps(out["notice_date"]).dt.normalize()
    known = out["notice_known"].fillna(False) & notice.notna()
    out["available_at"] = (notice + pd.Timedelta(days=1)).where(known)
    realized = end.notna() & (end + pd.Timedelta(days=1) <= cutoff)
    visible = realized & known & (out["available_at"] <= cutoff)
    unknown = realized & ~known
    not_yet = realized & known & (out["available_at"] > cutoff)
    stats = {"status": "partial" if unknown.any() else "ok", "input_rows": int(len(out)),
             "visible_rows": int(visible.sum()), "notice_unknown": int(unknown.sum()),
             "not_yet_available": int(not_yet.sum()), "future_event": int((~realized).sum())}
    return out.loc[visible].copy(), stats
