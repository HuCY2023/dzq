# -*- coding: utf-8 -*-
"""52w high-low from market lake — stub in standalone dzq.

Without ftqtz K-line lake, returns empty; ingest still writes legu_120d.
Wire your own OHLC store into fetch_52w_high_low when ready.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from data_source.dp_cycle._http import call_with_retry


def fetch_index_close(symbol: str = "sh000680") -> tuple[pd.DataFrame, dict[str, Any]]:
    meta: dict[str, Any] = {"source": f"akshare.stock_zh_index_daily({symbol})", "ok": False, "error": None}
    try:
        import akshare as ak

        df = call_with_retry(ak.stock_zh_index_daily, symbol=symbol)
        out = df.copy()
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
        out["close"] = pd.to_numeric(out["close"], errors="coerce")
        out = out[["date", "close"]].dropna().sort_values("date")
        meta["ok"] = True
        meta["rows"] = int(len(out))
        return out.reset_index(drop=True), meta
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return pd.DataFrame(columns=["date", "close"]), meta


def fetch_52w_high_low(
    *,
    start: str | None = "2025-10-01",
    window: int = 252,
    lake_root: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    del window, lake_root
    return pd.DataFrame(), {
        "ok": False,
        "error": "kline lake not bundled in dzq; use legu 120d or wire your lake",
        "source": "dzq_stub",
        "start": start,
    }


def merge_high_low_with_index(hl: pd.DataFrame, idx: pd.DataFrame) -> pd.DataFrame:
    if hl is None or getattr(hl, "empty", True):
        return pd.DataFrame()
    out = hl.copy()
    if idx is not None and not getattr(idx, "empty", True):
        out = out.merge(idx.rename(columns={"close": "index_close"}), on="date", how="left")
    return out
