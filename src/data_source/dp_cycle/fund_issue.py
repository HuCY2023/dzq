# -*- coding: utf-8 -*-
"""公募新成立：东财 fund_new_found_em，成立日 + 募集份额（亿份）。"""
from __future__ import annotations

from typing import Any

import pandas as pd

from data_source.dp_cycle._http import call_with_retry


def classify_fund_issue(fund_type: Any, name: Any) -> str:
    """按来源类型和名称线索粗分；权益类别仅为待核验候选。"""
    kind = "" if pd.isna(fund_type) else str(fund_type).strip()
    label = "" if pd.isna(name) else str(name).strip()
    if kind.startswith("QDII"):
        return "qdii"
    if kind.startswith("FOF"):
        return "fof"
    if kind.startswith("债券") or kind == "指数型-固收":
        return "bond"
    if kind.startswith("货币"):
        return "money"
    cross_border = ("港股", "港股通", "恒生", "香港", "H股", "海外", "全球", "纳指", "纳斯达克", "标普", "日经", "美股", "美国")
    if "海外" in kind or any(term in label for term in cross_border):
        return "cross_border_candidate"
    if kind in ("股票型", "指数型-股票", "混合型-偏股"):
        return "domestic_equity_candidate"
    if kind.startswith("混合型"):
        return "other_mixed"
    return "unknown"


def fetch_fund_issues() -> tuple[pd.DataFrame, dict[str, Any]]:
    meta: dict[str, Any] = {"source": "eastmoney.fund_new_found_em", "ok": False, "error": None}
    try:
        import akshare as ak

        raw = call_with_retry(ak.fund_new_found_em, retries=3, delay=1.5)
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return pd.DataFrame(), meta
    if raw is None or getattr(raw, "empty", True):
        meta["error"] = "empty"
        return pd.DataFrame(), meta
    out = pd.DataFrame(
        {
            "code": raw.iloc[:, 0].astype(str),
            "name": raw.iloc[:, 1].astype(str),
            "fund_type": raw.iloc[:, 3].astype(str),
            "shares_yi": pd.to_numeric(raw.iloc[:, 5], errors="coerce"),
            "found_date": pd.to_datetime(raw.iloc[:, 6], errors="coerce"),
        }
    )
    out = out.dropna(subset=["code", "found_date"])
    out = out.drop_duplicates("code", keep="last")
    meta["ok"] = True
    meta["rows"] = int(len(out))
    if not out.empty:
        meta["first"] = str(out["found_date"].min().date())
        meta["last"] = str(out["found_date"].max().date())
    return out.reset_index(drop=True), meta
