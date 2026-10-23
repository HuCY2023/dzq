# -*- coding: utf-8 -*-
"""东财行业资金流日频。免费窗口与大盘相同，大约 120 个交易日，靠日更累积。"""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
import requests

_LIST = "https://push2.eastmoney.com/api/qt/clist/get"
_KLINE = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/bkzj/hy.html",
}


def fetch_industry_flows() -> tuple[pd.DataFrame, dict[str, Any]]:
    meta: dict[str, Any] = {"source": "eastmoney.industry.fflow.daykline", "ok": False, "error": None}
    session = requests.Session()
    session.trust_env = False
    boards: list[tuple[str, str]] = []
    try:
        r = session.get(
            _LIST,
            params={
                "pn": "1",
                "pz": "200",
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f62",
                "fs": "m:90+t:2",
                "fields": "f12,f14",
                "ut": "b2884a393a59ad64002292a3e90d46a5",
            },
            headers=_HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        for row in ((r.json().get("data") or {}).get("diff")) or []:
            code = str(row.get("f12") or "").strip()
            name = str(row.get("f14") or "").strip()
            if code and name:
                boards.append((code, name))
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return pd.DataFrame(), meta
    meta["boards"] = len(boards)
    parts: list[pd.DataFrame] = []
    failed = 0
    for i, (code, name) in enumerate(boards):
        if i:
            time.sleep(0.15)
        try:
            r = session.get(
                _KLINE,
                params={
                    "lmt": "0",
                    "klt": "101",
                    "secid": f"90.{code}",
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54",
                    "ut": "b2884a393a59ad64002292a3e90d46a5",
                },
                headers=_HEADERS,
                timeout=25,
            )
            r.raise_for_status()
            klines = ((r.json().get("data") or {}).get("klines")) or []
        except Exception:
            failed += 1
            continue
        rows = []
        for line in klines:
            p = str(line).split(",")
            if len(p) < 4:
                continue
            try:
                rows.append(
                    {
                        "date": p[0],
                        "bk_code": code,
                        "name": name,
                        "net_yi": float(p[1]) / 1e8,
                        "small_yi": float(p[2]) / 1e8,
                        "mid_yi": float(p[3]) / 1e8,
                    }
                )
            except (TypeError, ValueError):
                continue
        if rows:
            parts.append(pd.DataFrame(rows))
    meta["failed"] = failed
    if not parts:
        meta["error"] = meta.get("error") or "no industry klines"
        return pd.DataFrame(), meta
    out = pd.concat(parts, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date", "net_yi"])
    out = out.drop_duplicates(["date", "bk_code"], keep="last")
    meta["ok"] = True
    meta["rows"] = int(len(out))
    meta["first"] = str(out["date"].min().date())
    meta["last"] = str(out["date"].max().date())
    return out.reset_index(drop=True), meta
