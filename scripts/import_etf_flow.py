"""Import an explicit ETF component snapshot as an observation, without network access."""
from __future__ import annotations

import argparse
from datetime import date
import io
import json
from pathlib import Path
import sys
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from data_source.dp_cycle.etf_flow import EVENTS_PATH, estimate_core_etf_flows
from data_source.dp_cycle.national_team_etf import CORE_CSI300


OUTPUT_ROOT = ROOT / "output" / "user" / "dp_cycle" / "etf_flow_imports"
CALENDAR_PATH = ROOT / "output" / "user" / "dp_cycle" / "trade_calendar.csv"
CALENDAR_ORIGIN = "akshare.tool_trade_date_hist_sina"


def _calendar(path: Path, reference_path: Path, observations: pd.DataFrame, as_of: pd.Timestamp) -> tuple[dict, dict, dict]:
    """Cross-check the cached calendar with the source report's index dates."""
    meta = {"path": str(path), "source": None, "coverage_start": None,
            "coverage_end": None, "coverage_verified": False, "checked_date_count": 0,
            "crosscheck_path": str(reference_path)}
    sources = {}
    errors = {}
    for name, source_path in (("calendar", path), ("crosscheck", reference_path)):
        try:
            sources[name] = source_path.read_bytes()
        except OSError as exc:
            errors[name] = str(exc)
    try:
        if "calendar" in errors:
            raise OSError(errors["calendar"])
        frame = pd.read_csv(io.BytesIO(sources["calendar"]), dtype=str, keep_default_na=False)
        if not {"date", "origin"}.issubset(frame.columns) or frame.empty:
            raise ValueError("日历缺少日期或来源")
        dates = pd.to_datetime(frame["date"], format="ISO8601", errors="coerce").dt.normalize()
        if dates.isna().any():
            raise ValueError("日历日期无效")
        meta["cache_end"] = dates.max().date().isoformat()
        before_cutoff = dates <= as_of
        dates = dates[before_cutoff]
        if dates.duplicated().any():
            raise ValueError("日历日期重复")
        origins = frame.loc[before_cutoff, "origin"].unique()
        if len(origins) != 1 or origins[0] != CALENDAR_ORIGIN:
            raise ValueError("日历来源未记录或不受支持")
        meta["source"] = origins[0]
        days = pd.DatetimeIndex(dates).sort_values()
        if days.empty:
            raise ValueError("截至日之前没有日历记录")
        if "crosscheck" in errors:
            raise OSError(errors["crosscheck"])
        crosscheck = pd.read_csv(io.BytesIO(sources["crosscheck"]), dtype=str, keep_default_na=False)
        if not {"date", "primary_close", "tencent"}.issubset(crosscheck.columns) or crosscheck.empty:
            raise ValueError("缺少指数日期交叉核对记录")
        reference_dates = pd.to_datetime(crosscheck["date"], format="ISO8601", errors="coerce").dt.normalize()
        if reference_dates.isna().any():
            raise ValueError("指数交叉核对日期无效")
        before_cutoff = reference_dates <= as_of
        reference_dates = reference_dates[before_cutoff]
        prices = crosscheck.loc[before_cutoff, ["primary_close", "tencent"]].apply(pd.to_numeric, errors="coerce")
        if reference_dates.duplicated().any() or prices.isna().any().any() or prices.le(0).any().any():
            raise ValueError("指数交叉核对记录不完整")
        reference = pd.DatetimeIndex(reference_dates).sort_values()
        if reference.empty:
            raise ValueError("截至日之前没有指数交叉核对记录")
        start, end = max(days[0], reference[0]), min(days[-1], reference[-1])
        if start > end or set(days[(days >= start) & (days <= end)]) != set(reference[(reference >= start) & (reference <= end)]):
            raise ValueError("交易日历与指数交叉表的日期不一致")
        meta["coverage_start"] = start.date().isoformat()
        meta["coverage_end"] = end.date().isoformat()
        meta["crosscheck_days"] = int(((reference >= start) & (reference <= end)).sum())
        referenced = pd.to_datetime(observations["date"], errors="coerce")
        if "previous_date" in observations:
            referenced = pd.concat([referenced, pd.to_datetime(observations["previous_date"], errors="coerce")])
        checked = set(referenced.dropna().dt.normalize())
        checked = {day for day in checked if start <= day <= end}
        meta["checked_date_count"] = len(checked)
        if not checked or checked.difference(days):
            raise ValueError("基金观察日期与日历不能相互核对")
        meta["coverage_verified"] = True
        meta["verification"] = "来源已记录，并与指数交叉表逐日比对，核对范围以双方重合区间为准。"
        return {"dates": days, "coverage_start": start, "coverage_end": end,
                "source": origins[0], "coverage_verified": True}, meta, sources
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        meta["reason"] = str(exc)
        return {}, meta, sources


def run_import(input_path: Path, calendar_path: Path, output_root: Path, *, as_of: str | None = None) -> dict:
    """Save raw input, computed components and daily observations under output_root.

    The source CSV must already contain same-day NAV. Filtering by observation date
    does not establish when any field was published or make the data point-in-time.
    """
    cutoff = pd.Timestamp(as_of if as_of is not None else date.today()).normalize()
    if cutoff > pd.Timestamp(date.today()):
        raise ValueError("截至日不能为未来日期")
    input_path, calendar_path, output_root = (Path(path).resolve() for path in (input_path, calendar_path, output_root))
    raw = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    if not {"date", "code"}.issubset(raw.columns):
        raise ValueError("输入缺少 date 或 code")
    input_dates = pd.to_datetime(raw["date"], format="ISO8601", errors="coerce").dt.normalize()
    if input_dates.isna().any():
        raise ValueError("输入存在无效观察日期")
    basket = raw["code"].str.zfill(6).isin(CORE_CSI300)
    future_rows = int((basket & (input_dates > cutoff)).sum())
    selected = raw.loc[basket & (input_dates <= cutoff)].copy()
    if selected.empty:
        raise ValueError("截至日没有四只基金的观察记录")

    calendar, calendar_meta, calendar_sources = _calendar(calendar_path, input_path.parent / "index-crosscheck.csv", selected, cutoff)
    observed_dates = pd.to_datetime(selected["date"])
    calculation_input = selected
    if calendar_meta["coverage_verified"] and pd.Timestamp(calendar_meta["coverage_end"]) > observed_dates.max():
        # The calculator uses a non-basket row to include missing trailing sessions.
        sentinel = {column: "" for column in selected.columns}
        sentinel.update(date=calendar_meta["coverage_end"], code="__calendar_end__")
        calculation_input = pd.concat([selected, pd.DataFrame([sentinel])], ignore_index=True)
    event_bytes = EVENTS_PATH.read_bytes()
    share_events = pd.read_csv(io.BytesIO(event_bytes), dtype={"code": str}, parse_dates=["event_date"])
    calculated = estimate_core_etf_flows(calculation_input, trading_dates=calendar, share_events=share_events)
    components, daily = calculated["components"], calculated["daily"]
    limitations = calculated["limitations"] + [
        "输入文件未独立记录净值观察日，调用方须确认净值与观察日同日。",
        "仅按观察日截断，无法证明份额、净值及其他字段在截至日当时已经发布。",
        "日历与随输入提供的指数交叉表比对，不能发现两份资料同时遗漏的日期。",
        "最近源数据日后的交易日标为待补充，可能尚未发布，不据此判定数据过期。",
    ]
    metadata = {
        "input": str(input_path.resolve()), "as_of": cutoff.date().isoformat(),
        "data_start": observed_dates.min().date().isoformat(),
        "data_end": observed_dates.max().date().isoformat(),
        "source_rows": len(selected), "future_input_rows": future_rows,
        "complete_days": int(daily["complete"].sum()),
        "partial_days": int((~daily["complete"]).sum()),
        "pending_tail_days": int((daily["date"] > observed_dates.max()).sum()) if calendar_meta["coverage_verified"] else None,
        "calendar": calendar_meta, "limitations": limitations,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    output_dir = Path(tempfile.mkdtemp(prefix="etf-flow-", dir=output_root))
    event_snapshot = output_dir / "etf_share_events.csv"
    event_snapshot.write_bytes(event_bytes)
    metadata["share_events"] = {"source": str(EVENTS_PATH.resolve()), "snapshot_path": str(event_snapshot)}
    for name, filename, key in (("calendar", "trade_calendar_source.csv", "snapshot_path"),
                                ("crosscheck", "index_crosscheck_source.csv", "crosscheck_snapshot_path")):
        snapshot_path = output_dir / filename
        if name in calendar_sources:
            snapshot_path.write_bytes(calendar_sources[name])
            calendar_meta[key] = str(snapshot_path)
        else:
            calendar_meta[key] = None
    selected.to_csv(output_dir / "source_components.csv", index=False, encoding="utf-8-sig")
    components.to_csv(output_dir / "components.csv", index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    daily.to_csv(output_dir / "daily.csv", index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    metadata["output_dir"] = str(output_dir)
    (output_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读导入四只宽基 ETF 的逐日份额和净值快照。")
    parser.add_argument("--input", required=True, help="现有 components.csv 的路径")
    parser.add_argument("--as-of", help="只保留不晚于该日期的观察，默认今天；不证明历史可得时间")
    args = parser.parse_args(argv)
    try:
        result = run_import(Path(args.input), CALENDAR_PATH, OUTPUT_ROOT, as_of=args.as_of)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 1
    print(f"观察日期：{result['data_start']} 至 {result['data_end']}；完整 {result['complete_days']} 天，部分 {result['partial_days']} 天。")
    print(f"截至日：{result['as_of']}；已核验交易日中最近源数据日后的待补充天数：{result['pending_tail_days']}。")
    print(f"交易日历：{result['calendar']['source']}，覆盖 {result['calendar']['coverage_start']} 至 {result['calendar']['coverage_end']}；核验 {result['calendar']['coverage_verified']}。")
    if result["calendar"].get("reason"):
        print(f"日历核验未通过：{result['calendar']['reason']}")
    print(f"输入中晚于截至日的四基金记录：{result['future_input_rows']} 条。")
    print(f"保存位置：{result['output_dir']}")
    for item in result["limitations"]:
        print(f"说明：{item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
