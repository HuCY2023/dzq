# -*- coding: utf-8 -*-
"""04 成长–价值偏离度：创业板50 vs 上证50 的 PE/PB 比。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from data_source.dp_cycle._http import call_with_retry

GROWTH_INDEX = "创业板50"
VALUE_INDEX = "上证50"
AUTHOR_THRESHOLD = 17.0


def _pe_pb(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    import akshare as ak

    pe = call_with_retry(ak.stock_index_pe_lg, symbol=symbol)
    pb = call_with_retry(ak.stock_index_pb_lg, symbol=symbol)
    return pe, pb


def fetch_growth_value_divergence() -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    候选公式（与 PDF 一致）：偏离度 = (PE比 + PB比) / 2
    PE比 = 成长滚动市盈率 / 价值滚动市盈率
    PB比 = 成长市净率 / 价值市净率
    """
    meta: dict[str, Any] = {
        "source": "legulegu via akshare stock_index_pe_lg/pb_lg",
        "growth": GROWTH_INDEX,
        "value": VALUE_INDEX,
        "formula": "(PE比+PB比)/2",
        "author_threshold": AUTHOR_THRESHOLD,
        "ok": False,
        "error": None,
    }
    try:
        g_pe, g_pb = _pe_pb(GROWTH_INDEX)
        v_pe, v_pb = _pe_pb(VALUE_INDEX)
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return pd.DataFrame(), meta

    def _prep_pe(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
        out = df.copy()
        out["date"] = pd.to_datetime(out["日期"], errors="coerce").dt.normalize()
        col = "滚动市盈率" if "滚动市盈率" in out.columns else out.columns[3]
        out[f"{prefix}_pe"] = pd.to_numeric(out[col], errors="coerce")
        return out[["date", f"{prefix}_pe"]].dropna()

    def _prep_pb(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
        out = df.copy()
        out["date"] = pd.to_datetime(out["日期"], errors="coerce").dt.normalize()
        col = "市净率" if "市净率" in out.columns else out.columns[2]
        out[f"{prefix}_pb"] = pd.to_numeric(out[col], errors="coerce")
        return out[["date", f"{prefix}_pb"]].dropna()

    m = (
        _prep_pe(g_pe, "g")
        .merge(_prep_pe(v_pe, "v"), on="date", how="inner")
        .merge(_prep_pb(g_pb, "g"), on="date", how="inner")
        .merge(_prep_pb(v_pb, "v"), on="date", how="inner")
        .sort_values("date")
    )
    m["PE比"] = m["g_pe"] / m["v_pe"]
    m["PB比"] = m["g_pb"] / m["v_pb"]
    m["偏离度"] = (m["PE比"] + m["PB比"]) / 2.0
    m["作者阈值_17"] = AUTHOR_THRESHOLD
    meta["ok"] = True
    meta["rows"] = int(len(m))
    if len(m):
        meta["last_date"] = str(m["date"].iloc[-1].date())
        meta["last_偏离度"] = float(m["偏离度"].iloc[-1])
    return m.reset_index(drop=True), meta
