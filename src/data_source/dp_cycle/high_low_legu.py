# -*- coding: utf-8 -*-
"""05 广度快径：乐咕 high-low-statistics（直连，绕开 akshare 旧日期解析）。

注意：接口给的是 20/60/120 日新高新低家数，**不是**完整 52 周。
报告里标注为「120 日」；完整 52 周仍走 K 线湖 `high_low_lake`。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import requests

_URL = "https://www.legulegu.com/stockdata/member-ship/get-high-low-statistics/{symbol}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.legulegu.com/stockdata/high-low-statistics",
}


def fetch_legu_high_low(symbol: str = "all") -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    symbol: all / sz50 / hs300 / zz500
    返回列：date, new_high, new_low, diff, close  （new_* 取 high120/low120）
    """
    meta: dict[str, Any] = {
        "source": f"legulegu.get-high-low-statistics/{symbol}",
        "ok": False,
        "error": None,
        "window": "120d",
        "note": "乐咕120日新高新低；≠作者52周口径；完整52周用K线湖",
    }
    try:
        session = requests.Session()
        session.trust_env = False
        r = session.get(_URL.format(symbol=symbol), headers=_HEADERS, timeout=30)
        r.raise_for_status()
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("empty response")
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
        for c in ("high20", "low20", "high60", "low60", "high120", "low120", "close"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        out = pd.DataFrame(
            {
                "date": df["date"],
                "new_high": df.get("high120"),
                "new_low": df.get("low120"),
                "index_close": df.get("close"),
            }
        )
        out = out.dropna(subset=["date"]).sort_values("date")
        out["diff"] = out["new_high"] - out["new_low"]
        out["universe"] = pd.NA
        meta["ok"] = True
        meta["rows"] = int(len(out))
        meta["first_date"] = str(out["date"].iloc[0].date()) if len(out) else None
        meta["last_date"] = str(out["date"].iloc[-1].date()) if len(out) else None
        return out.reset_index(drop=True), meta
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return pd.DataFrame(), meta
