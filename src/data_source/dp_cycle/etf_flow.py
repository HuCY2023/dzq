"""固定四只宽基 ETF 的逐基金份额变动和净值折算估计。

计算使用调用者提供的数据；快照摘要只读调用者指定的目录。
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from data_source.dp_cycle.national_team_etf import CORE_CSI300


EVENTS_PATH = Path(__file__).resolve().parents[3] / "research" / "DP" / "data" / "etf_share_events.csv"
LIMITATIONS = (
    "份额变化按同日单位净值折算，是申赎估计，不是实际现金流。",
    "场内份额变化可能包含跨系统转托管，不能识别机构身份。",
    "历史公告时点和历史版本未核验，本结果不能用于历史可交易回测。",
    "逐日可比性依赖调用者提供带覆盖范围且已核验完整的交易日历。",
    "输入净值须为观察日同日单位净值，调用方应先按 as_of 和可得时间截断。",
)


def estimate_core_etf_flows(
    observations: pd.DataFrame, *, trading_dates: Mapping[str, Any] | None = None,
    share_events: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """计算四只 ETF 的逐日估算，仅当四只均可比时输出日合计。

    输入列为 date、code、previous_date、shares、previous_shares、split_factor、nav；
    份额以份计，nav 须为 date 的同日单位净值，单位元/份。调用方必须先按 as_of
    和数据可得时间截断输入；本函数不推定公告时间或历史版本。
    trading_dates 须含 dates、coverage_start、coverage_end、source 和
    coverage_verified=True，表示调用方已核验该覆盖范围的完整交易日历。
    previous_date 须匹配上一交易日；缺少可信日历时不推断日期连续性。
    返回 components、daily 和 limitations。
    """
    if not isinstance(observations, pd.DataFrame) or not {"date", "code"}.issubset(observations.columns):
        raise ValueError("observations must have date and code columns")

    raw = observations.copy()
    if "status" in raw.columns:
        raw["source_status"] = raw.pop("status")
    elif "source_status" not in raw.columns:
        raw["source_status"] = pd.NA
    for name in ("previous_date", "shares", "previous_shares", "split_factor", "nav"):
        if name not in raw.columns:
            raw[name] = pd.NA
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce").dt.normalize()
    raw["previous_date"] = pd.to_datetime(raw["previous_date"], errors="coerce").dt.normalize()
    raw["code"] = raw["code"].astype("string").str.zfill(6)
    dates = sorted(pd.DatetimeIndex(raw["date"].dropna().unique()))
    raw = raw.loc[raw["date"].notna() & raw["code"].isin(CORE_CSI300)].copy()
    duplicate = set(map(tuple, raw.loc[raw.duplicated(["date", "code"], keep=False), ["date", "code"]].to_numpy()))
    raw = raw.drop_duplicates(["date", "code"]).set_index(["date", "code"])
    calendar = pd.DatetimeIndex([])
    coverage_start = coverage_end = pd.NaT
    if (isinstance(trading_dates, Mapping) and trading_dates.get("coverage_verified") is True
            and trading_dates.get("source") and "dates" in trading_dates):
        coverage_start = pd.to_datetime(trading_dates.get("coverage_start"), errors="coerce")
        coverage_end = pd.to_datetime(trading_dates.get("coverage_end"), errors="coerce")
        if pd.notna(coverage_start) and pd.notna(coverage_end) and coverage_start <= coverage_end:
            calendar = pd.DatetimeIndex(pd.to_datetime(list(trading_dates["dates"]), errors="coerce")).dropna().normalize().unique().sort_values()
    if dates and not calendar.empty:
        dates = sorted(set(dates) | set(calendar[(calendar >= dates[0]) & (calendar <= dates[-1])]))
    index = pd.MultiIndex.from_product([dates, CORE_CSI300], names=["date", "code"])
    present = index.isin(raw.index)
    result = raw.reindex(index).reset_index()
    result["observed"] = present
    for name in ("shares", "previous_shares", "split_factor", "nav"):
        result[name] = pd.to_numeric(result[name], errors="coerce").astype(float)
        result[name] = result[name].where(np.isfinite(result[name]))

    prior = {day: calendar[i - 1] for i, day in enumerate(calendar) if i > 0}
    result["expected_previous_date"] = result["date"].map(prior)
    result["status"] = "ok"

    def mark(mask: pd.Series, status: str) -> None:
        result.loc[(result["status"] == "ok") & mask, "status"] = status

    mark(~result["observed"], "missing_fund")
    mark(result["shares"].isna(), "missing_shares")
    mark(result["previous_shares"].isna(), "missing_previous_shares")
    mark(result["split_factor"].isna() | result["split_factor"].le(0), "missing_split_factor")
    mark(result["nav"].isna() | result["nav"].le(0), "missing_nav")
    mark(result["previous_date"].isna(), "missing_previous_date")
    mark(result["source_status"].notna() & ~result["source_status"].isin(["ok", "split_adjusted"]), "source_incomplete")
    if calendar.empty:
        mark(result["observed"], "calendar_unavailable")
    else:
        mark(result["expected_previous_date"].isna(), "calendar_unavailable")
        mark((result["expected_previous_date"] < coverage_start) | (result["date"] > coverage_end), "calendar_unavailable")
        mark(result["previous_date"] != result["expected_previous_date"], "previous_date_not_consecutive")
    if duplicate:
        mark(pd.Series([(day, code) in duplicate for day, code in zip(result["date"], result["code"])], index=result.index), "duplicate_fund")

    previous_keys = pd.MultiIndex.from_arrays([result["previous_date"], result["code"]], names=["date", "code"])
    previous_rows = raw.reindex(previous_keys).reset_index(drop=True)
    previous_present = pd.Series(previous_keys.isin(raw.index), index=result.index)
    previous_observed = pd.to_numeric(previous_rows["shares"], errors="coerce").astype(float)
    result["previous_observed_shares"] = previous_observed
    prior_duplicate = pd.Series([(day, code) in duplicate for day, code in zip(result["previous_date"], result["code"])], index=result.index)
    bad_prior_status = previous_rows["source_status"].notna() & ~previous_rows["source_status"].isin(["ok", "split_adjusted"])
    mark(previous_present & (previous_observed.isna() | prior_duplicate | bad_prior_status), "previous_record_unavailable")
    same_shares = pd.Series(np.isclose(result["previous_shares"], previous_observed, rtol=0, atol=1e-6), index=result.index)
    mark(previous_present & ~same_shares, "previous_shares_mismatch")

    events = (share_events if share_events is not None else
              pd.read_csv(EVENTS_PATH, dtype={"code": str}, parse_dates=["event_date"]))
    registered = {(pd.Timestamp(row.event_date).normalize(), str(row.code).zfill(6)): float(row.ratio)
                  for row in events.itertuples(index=False)}
    result["expected_split_factor"] = [registered.get((day, code), np.nan) for day, code in zip(result["date"], result["code"])]
    same_factor = pd.Series(np.isclose(result["split_factor"], result["expected_split_factor"], rtol=0, atol=1e-12), index=result.index)
    mark(result["expected_split_factor"].notna() & ~same_factor, "event_factor_mismatch")
    mark(result["expected_split_factor"].isna() & ~np.isclose(result["split_factor"], 1.0, rtol=0, atol=1e-12), "event_factor_unverified")

    values = (result["shares"] - result["split_factor"] * result["previous_shares"]) * result["nav"] / 1e8
    result["flow_yi"] = values.where(result["status"] == "ok")
    daily = result.groupby("date", sort=True).agg(
        observed_count=("observed", "sum"),
        comparable_count=("status", lambda items: int((items == "ok").sum())),
        flow_yi=("flow_yi", lambda values: values.sum(min_count=len(CORE_CSI300))),
    ).reset_index()
    daily["expected_count"] = len(CORE_CSI300)
    daily["complete"] = daily["comparable_count"] == daily["expected_count"]
    daily["status"] = np.where(daily["complete"], "ok", "partial")
    return {"components": result, "daily": daily, "limitations": list(LIMITATIONS)}


def load_latest_etf_flow_snapshot(import_root: str | Path, *, as_of: Any | None = None) -> dict[str, Any]:
    """只读最近一次导入快照；按 metadata.json 修改时间选取，同时间按路径排序。

    摘要截至 as_of（默认今天），保留最后一日的缺失和部分状态，不回退到完整日。
    快照只能提供现在保存的历史数据，不能证明历史时点的公告或版本可得性。
    """
    cutoff = pd.Timestamp(as_of if as_of is not None else date.today()).normalize()
    if pd.isna(cutoff) or cutoff > pd.Timestamp(date.today()):
        raise ValueError("截至日无效或为未来日期")
    selection = "按快照 metadata.json 修改时间选择最近；同时间按目录路径排序，不回退旧快照。"
    result = {
        "status": "unavailable", "as_of": cutoff.date().isoformat(), "snapshot_as_of": None,
        "date": None, "latest_observation_date": None, "components": [], "flow_yi": None,
        "coverage": {"observed": 0, "comparable": 0, "expected": len(CORE_CSI300),
                     "complete": False, "calendar_covers_as_of": False, "snapshot_covers_as_of": False},
        "source": {"snapshot": None, "input": None, "calendar": None},
        "snapshot_selection": selection, "limitations": list(LIMITATIONS), "reason": None,
    }
    paths = list(Path(import_root).glob("*/metadata.json"))
    if not paths:
        result["reason"] = "未找到导入快照。"
        return result
    meta_path = max(paths, key=lambda path: (path.stat().st_mtime_ns, str(path)))
    snapshot = meta_path.parent.resolve()
    result["source"]["snapshot"] = str(snapshot)

    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        snapshot_as_of = pd.Timestamp(metadata["as_of"]).normalize()
        if pd.isna(snapshot_as_of):
            raise ValueError("快照缺少截至日")
        result["snapshot_as_of"] = snapshot_as_of.date().isoformat()
        result["source"].update(input=metadata.get("input"), calendar=metadata.get("calendar"))
        result["limitations"] = list(dict.fromkeys([*LIMITATIONS, *metadata.get("limitations", [])]))
        events_recorded = bool(metadata.get("share_events") and (snapshot / "etf_share_events.csv").is_file())
        result["source"]["events_recorded"] = events_recorded
        if not events_recorded:
            result["limitations"].append("此旧导入未保存当时使用的份额事件表，事件表版本不可追溯。")
        daily = pd.read_csv(snapshot / "daily.csv")
        components = pd.read_csv(snapshot / "components.csv", dtype={"code": str})
        if not {"date", "status", "complete", "flow_yi"}.issubset(daily.columns):
            raise ValueError("快照缺少逐日字段")
        if not {"date", "code", "observed", "status", "flow_yi"}.issubset(components.columns):
            raise ValueError("快照缺少逐基金字段")
        for frame in (daily, components):
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
            if frame["date"].isna().any():
                raise ValueError("快照日期无效")
        effective_cutoff = min(cutoff, snapshot_as_of)
        daily = daily.loc[daily["date"] <= effective_cutoff].sort_values("date")
        if daily.empty:
            result["reason"] = "截至日之前没有快照记录。"
            return result

        last = daily.iloc[-1]
        last_day = last["date"]
        calendar = metadata.get("calendar") or {}
        start = pd.to_datetime(calendar.get("coverage_start"), errors="coerce")
        end = pd.to_datetime(calendar.get("coverage_end"), errors="coerce")
        verified = calendar.get("coverage_verified") is True and pd.notna(start) and pd.notna(end) and start <= end
        expected_day = None
        if verified and start <= effective_cutoff:
            if end <= effective_cutoff:
                expected_day = end
            elif last_day == effective_cutoff:
                expected_day = last_day
            else:
                calendar_path = snapshot / "trade_calendar_source.csv"
                if calendar_path.exists():
                    calendar_days = pd.to_datetime(pd.read_csv(calendar_path)["date"], errors="coerce").dropna().dt.normalize()
                    calendar_days = calendar_days[(calendar_days >= start) & (calendar_days <= effective_cutoff)]
                    if not calendar_days.empty:
                        expected_day = calendar_days.max()
        if expected_day is not None and expected_day > last_day:
            last_day = expected_day
            last = pd.Series({"status": "partial", "complete": False, "flow_yi": None})

        def truth(value: Any) -> bool:
            return str(value).lower() in ("true", "1")

        def clean(value: Any) -> Any:
            if pd.isna(value) or isinstance(value, (float, np.floating)) and not np.isfinite(value):
                return None
            return value.item() if isinstance(value, np.generic) else value

        components = components.loc[components["date"] <= last_day]
        components["code"] = components["code"].astype("string").str.zfill(6)
        observations = components.loc[components["observed"].map(truth) & components["code"].isin(CORE_CSI300), "date"]
        if not observations.empty:
            result["latest_observation_date"] = observations.max().date().isoformat()
        fields = ("name", "previous_date", "shares", "previous_shares", "split_factor", "nav",
                  "source_status", "shares_source", "split_source", "flow_yi")
        items = []
        for code in CORE_CSI300:
            rows = components.loc[(components["date"] == last_day) & (components["code"] == code)]
            item = {"code": code, "observed": False, "status": "snapshot_component_missing",
                    **{field: None for field in fields}}
            if len(rows) == 1:
                row = rows.iloc[0]
                item.update({field: clean(row.get(field)) for field in fields})
                item.update(observed=truth(row["observed"]), status=clean(row["status"]))
                if item["status"] == "ok" and (not item["observed"] or item["flow_yi"] is None):
                    item["status"] = "snapshot_component_unavailable"
            elif len(rows) > 1:
                item["status"] = "snapshot_component_duplicate"
                item["observed"] = bool(rows["observed"].map(truth).any())
            items.append(item)

        observed_count = sum(item["observed"] for item in items)
        comparable_count = sum(item["status"] == "ok" for item in items)
        total = clean(last["flow_yi"])
        complete = (comparable_count == len(CORE_CSI300) and last["status"] == "ok"
                    and truth(last["complete"]) and total is not None)
        calendar_covers_as_of = verified and start <= cutoff <= end
        covers_as_of = cutoff <= snapshot_as_of and calendar_covers_as_of and expected_day is not None
        reason = None
        if not calendar_covers_as_of:
            coverage_end = end.date().isoformat() if verified else "未核验"
            latest = result["latest_observation_date"] or "暂无"
            reason = f"交易日历未核验覆盖截至日（已核验至 {coverage_end}），无法确认该日是否为非交易日；最新实际观察日为 {latest}。"
        elif cutoff > snapshot_as_of:
            reason = f"快照截至 {snapshot_as_of.date().isoformat()}，其后覆盖未知。"
        elif expected_day is None:
            reason = "缺少日历副本，无法确认截至日对应的交易日。"
        elif not complete:
            reason = "最新记录日不完整；当日数据可能尚未发布。"
        result.update(
            status="ok" if complete and covers_as_of else "partial",
            date=last_day.date().isoformat(), components=items,
            flow_yi=total if complete else None,
            coverage={"observed": observed_count, "comparable": comparable_count,
                      "expected": len(CORE_CSI300), "complete": complete,
                      "calendar_covers_as_of": bool(calendar_covers_as_of), "snapshot_covers_as_of": covers_as_of},
            reason=reason,
        )
    except (OSError, ValueError, KeyError, TypeError, pd.errors.ParserError) as exc:
        result["reason"] = f"快照不可读取：{exc}"
    return result
