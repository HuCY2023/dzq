"""已核验的基金定期报告：报告期与公告日期分别记录。"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from data_source.dp_cycle.margin import _margin_local_timestamp
from data_source.dp_cycle.national_team_etf import CORE_CSI300


DISCLOSURES_PATH = Path(__file__).resolve().parents[3] / "research" / "DP" / "data" / "etf_holder_disclosures.csv"


def load_holder_disclosures(*, as_of: Any | None = None, path: Path = DISCLOSURES_PATH) -> dict[str, Any]:
    """日期只有日精度的公告从次日可见，较新报告不含实名时不能沿用旧持仓。"""
    cutoff = _margin_local_timestamp(as_of if as_of is not None else date.today()).date()
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    visible = [row for row in rows if row["period_end"] <= cutoff.isoformat() and row["published_date"] < cutoff.isoformat()]
    items = []
    for code in CORE_CSI300:
        reports = sorted((row for row in visible if row["code"] == code),
                         key=lambda row: (row["period_end"], row["published_date"]))
        current: dict[str, Any] = {"code": code, "status": "not_verified", "period_end": None,
                                   "published_date": None, "reported_total_shares": None,
                                   "direct_shares": None, "source_url": None, "source_page": None,
                                   "last_named": None}
        if reports:
            latest = reports[-1]
            current.update(status=latest["status"], period_end=latest["period_end"],
                           published_date=latest["published_date"],
                           reported_total_shares=int(latest["reported_total_shares"]),
                           source_url=latest["source_url"], source_page=int(latest["source_page"]))
            named = [row for row in reports if row["status"] == "named_disclosure"]
            if latest["status"] == "named_disclosure":
                current["direct_shares"] = int(latest["investment_shares"]) + int(latest["asset_shares"])
            elif named:
                prior = named[-1]
                current["last_named"] = {"period_end": prior["period_end"], "published_date": prior["published_date"],
                                         "direct_shares": int(prior["investment_shares"]) + int(prior["asset_shares"]),
                                         "source_url": prior["source_url"], "source_page": int(prior["source_page"])}
        items.append(current)
    verified = sum(row["status"] != "not_verified" for row in items)
    named = sum(row["status"] == "named_disclosure" for row in items)
    display = (f"已核验基金报告 {verified}/{len(CORE_CSI300)} 只；未披露不等于零，"
               "历史快照不能作为当前持仓。")
    pdf_note = f"持有人报告已核验 {verified}/{len(CORE_CSI300)} 只，旧期实名份额不是当前持仓。"
    if verified == len(items) and all(row["status"] == "not_disclosed" for row in items):
        periods = {row["period_end"] for row in items}
        if len(periods) == 1:
            published = sorted({row["published_date"] for row in items})
            pdf_note = (f"持有人报告已核验 {verified}/{len(items)} 只：{periods.pop()} 均未列实名；"
                        f"公告 {'、'.join(published)}，旧期非当前。")
    return {"status": "ok" if named == len(CORE_CSI300) else "partial", "as_of": cutoff.isoformat(),
            "coverage": {"verified_reports": verified, "named_latest_report": named, "expected": len(CORE_CSI300)},
            "items": items, "display": display, "pdf_note": pdf_note,
            "note": "只记录已核验的定期报告；未披露不等于零。持有人快照不代表当日申赎或最新交易。"}
