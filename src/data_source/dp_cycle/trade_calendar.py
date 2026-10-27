"""Load the cached A-share trading dates; refresh only when explicitly requested."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd


def _dates(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    if frame.empty or column not in frame:
        raise ValueError("empty or missing trading dates")
    dates = pd.to_datetime(frame[column].astype("string"), format="ISO8601", errors="coerce")
    if dates.isna().any():
        raise ValueError("invalid trading dates")
    dates = dates.dt.normalize().drop_duplicates().sort_values().reset_index(drop=True)
    return pd.DataFrame({"date": dates})


def _save(path: Path, frame: pd.DataFrame) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            frame.assign(origin="akshare.tool_trade_date_hist_sina").to_csv(file, index=False, date_format="%Y-%m-%d")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_trade_calendar(*, refresh: bool = False) -> tuple[pd.DataFrame, dict]:
    """Return sorted dates and metadata, retaining the origin when recorded."""
    from output_paths import dp_cycle_store_dir

    path = Path(dp_cycle_store_dir()) / "trade_calendar.csv"
    dates = pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]")})
    error = None
    origin = None
    try:
        cached = pd.read_csv(path)
        dates = _dates(cached, "date")
        if "origin" in cached.columns:
            origins = cached["origin"].dropna().astype(str).unique()
            if len(origins) == 1:
                origin = origins[0]
    except Exception as exc:
        error = f"cache unavailable: {type(exc).__name__}"

    source = "cache" if not dates.empty else None
    status = "cached" if source else "failed"
    refresh_status = "not_requested"
    if refresh:
        try:
            import akshare as ak
            from data_source.dp_cycle._http import call_with_retry

            fetched = _dates(call_with_retry(ak.tool_trade_date_hist_sina, retries=1), "trade_date")
            if not dates.empty and (fetched["date"].iloc[0] > dates["date"].iloc[0] or fetched["date"].iloc[-1] < dates["date"].iloc[-1]):
                refresh_status = "failed"
                error = "refresh failed: coverage shrank"
            else:
                _save(path, fetched)
                dates = fetched
                source = "akshare.tool_trade_date_hist_sina"
                origin = source
                status = refresh_status = "updated"
                error = None
        except Exception as exc:
            refresh_status = "failed"
            error = f"refresh failed: {type(exc).__name__}"

    meta = {
        "ok": not dates.empty,
        "source": source,
        "origin": origin,
        "status": status,
        "refresh_status": refresh_status,
        "start_date": dates["date"].iloc[0].strftime("%Y-%m-%d") if not dates.empty else None,
        "end_date": dates["date"].iloc[-1].strftime("%Y-%m-%d") if not dates.empty else None,
        "error": error,
    }
    return dates, meta
