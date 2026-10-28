"""Import a dated partial-market turnover observation; never infer fund flows."""
from __future__ import annotations

from datetime import date
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from data_source.dp_cycle.margin import _margin_local_timestamp


def build_observation(summary: dict[str, Any], top: pd.DataFrame) -> dict[str, Any]:
    """Recalculate the leading groups using a report's recorded denominator."""
    latest = summary["latest"]
    stocks, total = int(latest["stocks"]), float(latest["total"])
    counts = {str(p): math.ceil(stocks * p / 100) for p in (1, 3, 5)}
    if stocks <= 0 or not math.isfinite(total) or total <= 0:
        raise ValueError("本地股票数或总成交额无效")
    if not {"symbol", "amount_yuan"}.issubset(top.columns) or len(top) != counts["5"] or top["symbol"].duplicated().any():
        raise ValueError("前5%成分数量或证券代码不一致")
    amounts = pd.to_numeric(top["amount_yuan"], errors="coerce")
    if amounts.isna().any() or (~np.isfinite(amounts) | amounts.le(0)).any() or not amounts.is_monotonic_decreasing:
        raise ValueError("前5%成交额缺失、无效或未按成交额排序")
    shares = {str(p): float(amounts.iloc[:counts[str(p)]].sum() / (total * 1e8) * 100) for p in (1, 3, 5)}
    absolute = float(amounts.sum() / 1e8)
    if absolute > total + 0.01:
        raise ValueError("前5%成交额不能超过样本总成交额")
    if any(int(latest[f"n{p}"]) != counts[str(p)] or abs(float(latest[f"share{p}"]) - shares[str(p)]) > 0.001
           for p in (1, 3, 5)) or abs(float(latest["abs5"]) - absolute) > 0.01:
        raise ValueError("前1%/3%/5%成交额与报告合计不一致")
    data_date = pd.Timestamp(summary["data_date"]).date().isoformat()
    generated_at = _margin_local_timestamp(summary["generated_at"]).isoformat()
    requested = pd.Timestamp(summary["as_of"]).date().isoformat()
    if data_date > requested:
        raise ValueError("观察日期晚于原报告请求截止日")
    return {
        "status": "partial", "date": data_date, "source_requested_as_of": requested,
        "source_generated_at": generated_at, "source_status": summary.get("status"),
        "stocks": stocks, "total_amount_yi": total, "top_counts": counts,
        "shares_pct": shares, "top5_amount_yi": absolute,
        "note": "仅为本地沪深京A股有效成交样本，尚未与完整证券名录核对；不参与评分，成交额不是净流入。"
                "历史股票池和复权未证明，不用于可交易回测；该快照需手动重新导入。",
    }


def load_latest_turnover_snapshot(root: str | Path, *, as_of: Any | None = None) -> dict[str, Any]:
    cutoff = _margin_local_timestamp(as_of if as_of is not None else date.today()).normalize()
    paths = list(Path(root).glob("*/observation.json"))
    if not paths:
        return {"status": "unavailable", "date": None, "reason": "尚未导入本地成交集中度快照。"}
    path = max(paths, key=lambda item: (item.stat().st_mtime_ns, str(item)))
    try:
        if any(not (path.parent / name).is_file() for name in ("source_summary.json", "latest-top5.csv")):
            raise ValueError("缺少导入时的来源文件")
        row = json.loads(path.read_text(encoding="utf-8"))
        observed = pd.Timestamp(row["date"]).normalize()
        generated = _margin_local_timestamp(row["source_generated_at"]).normalize()
        if observed > cutoff or generated > cutoff:
            return {"status": "unavailable", "date": None, "reason": "该本地快照在所选日期尚不可用。"}
        row["snapshot"] = str(path.parent.resolve())
        return row
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return {"status": "unavailable", "date": None, "reason": "本地成交集中度快照无法读取。"}
