# -*- coding: utf-8 -*-
"""重要股东减持：东财 RPT_SHARE_HOLDER_INCREASE，按变动截止日。

CHANGE_NUM 为万股。金额（亿元）= |万股| × 收盘价 / 10000。
"""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
import requests

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
    out = out.dropna(subset=["code", "end_date", "change_wan"])
    out["notice_date"] = out["notice_date"].fillna(out["end_date"])
    out = out.drop_duplicates(["code", "holder", "end_date", "notice_date"], keep="last")
    meta["ok"] = True
    meta["rows"] = int(len(out))
    return out.reset_index(drop=True), meta
