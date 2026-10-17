# -*- coding: utf-8 -*-
"""辅源：北向资金日净买（东财 HSGT）。≠ 主力大单，但有 2014+ 长历史可对齐周 K。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from data_source.dp_cycle._http import call_with_retry


def fetch_northbound_daily() -> tuple[pd.DataFrame, dict[str, Any]]:
    import akshare as ak

    meta: dict[str, Any] = {
        "source": "akshare.stock_hsgt_hist_em(北向资金)",
        "ok": False,
        "error": None,
        "note": "北向成交净买额（亿元量级以接口为准）；辅源≠视频主力大单",
    }
    try:
        raw = call_with_retry(ak.stock_hsgt_hist_em, symbol="北向资金", retries=4, delay=1.5)
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return pd.DataFrame(), meta

    df = raw.copy()
    df["date"] = pd.to_datetime(df["日期"], errors="coerce").dt.normalize()
    # 当日成交净买额 单位多为亿元
    net = pd.to_numeric(df.get("当日成交净买额"), errors="coerce")
    if net.isna().all() and "当日资金流入" in df.columns:
        net = pd.to_numeric(df["当日资金流入"], errors="coerce")
    out = pd.DataFrame({"date": df["date"], "净额_亿元": net})
    out = out.dropna(subset=["date"]).sort_values("date")
    # 保留有净额的行；部分末日 NaN
    out = out.dropna(subset=["净额_亿元"])
    meta["ok"] = not out.empty
    meta["rows"] = int(len(out))
    if not out.empty:
        meta["first_date"] = str(out["date"].iloc[0].date())
        meta["last_date"] = str(out["date"].iloc[-1].date())
        meta["last_净额_亿元"] = float(out["净额_亿元"].iloc[-1])
    return out.reset_index(drop=True), meta
