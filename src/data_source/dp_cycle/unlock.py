# -*- coding: utf-8 -*-
"""06 限售解禁金额：东财日统计 → 月/季，全市场与科创板。

口径：实际解禁市值（亿元）。接口 MARKET_CAP 单位为万元，除以 10000。
全市场 INDEX_CODE=000300（MARKET_TYPE=全部股票，不是沪深300指数本身）。
科创板 INDEX_CODE=000688。科创板是全市场的子集。
计划解禁市值另存，网页主图用实际解禁市值。
"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd
import requests

_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/dxf/marketStatistics.html",
}

# 东财解禁日统计里的市场代码。000300 在这张表上是「全部股票」。
UNIVERSE_INDEX = {
    "all": "000300",
    "star": "000688",
}


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def _get_page(session: requests.Session, params: dict[str, Any], *, retries: int = 4) -> dict[str, Any]:
    last: Exception | None = None
    for i in range(max(1, retries)):
        try:
            if i:
                time.sleep(0.8 * i)
            resp = session.get(_URL, params=params, headers=_HEADERS, timeout=40)
            resp.raise_for_status()
            result = (resp.json() or {}).get("result") or {}
            if not isinstance(result, dict):
                raise RuntimeError("empty result")
            return result
        except Exception as exc:  # noqa: BLE001
            last = exc
    assert last is not None
    raise last


def _rows_to_frame(rows: list[dict[str, Any]], universe: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["date", "universe", "amount_yi", "plan_yi", "stock_n", "source"])
    out = pd.DataFrame(rows)
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(out.get("FREE_DATE"), errors="coerce").dt.normalize(),
            "universe": universe,
            "amount_yi": pd.to_numeric(out.get("MARKET_CAP"), errors="coerce") / 10000.0,
            "plan_yi": pd.to_numeric(out.get("PLAN_MARKET_CAP"), errors="coerce") / 10000.0,
            "stock_n": pd.to_numeric(out.get("LIFT_ORG_NUM"), errors="coerce"),
            "source": "em_liftday_sta",
        }
    )
    frame = frame.dropna(subset=["date", "amount_yi"])
    return frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_universe_daily(
    universe: str,
    *,
    start: str = "2015-01-01",
    end: str | None = None,
    session: requests.Session | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """拉取一个市场的解禁日统计（含未来已排期）。"""
    code = UNIVERSE_INDEX[universe]
    meta: dict[str, Any] = {
        "universe": universe,
        "index_code": code,
        "source": "eastmoney.RPT_LIFTDAY_STA",
        "ok": False,
        "error": None,
    }
    sess = session or _session()
    filt = f'(INDEX_CODE="{code}")(FREE_DATE>=\'{start[:10]}\')'
    if end:
        filt += f'(FREE_DATE<=\'{end[:10]}\')'
    params = {
        "sortColumns": "FREE_DATE",
        "sortTypes": "1",
        "pageSize": "500",
        "pageNumber": "1",
        "columns": "FREE_DATE,LIFT_ORG_NUM,MARKET_CAP,PLAN_MARKET_CAP,INDEX_CODE,MARKET_TYPE",
        "source": "WEB",
        "client": "WEB",
        "reportName": "RPT_LIFTDAY_STA",
        "filter": filt,
    }
    try:
        first = _get_page(sess, params)
        pages = int(first.get("pages") or 1)
        count = int(first.get("count") or 0)
        rows = list(first.get("data") or [])
        for page in range(2, pages + 1):
            params["pageNumber"] = str(page)
            chunk = _get_page(sess, params)
            rows.extend(chunk.get("data") or [])
        frame = _rows_to_frame(rows, universe)
        if count and len(frame) < count * 0.9:
            raise RuntimeError(f"short page {len(frame)}/{count}")
        meta["ok"] = not frame.empty
        meta["rows"] = int(len(frame))
        meta["api_count"] = count
        if not frame.empty:
            meta["first_date"] = str(frame["date"].iloc[0].date())
            meta["last_date"] = str(frame["date"].iloc[-1].date())
        return frame, meta
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return pd.DataFrame(columns=["date", "universe", "amount_yi", "plan_yi", "stock_n", "source"]), meta


def fetch_unlock_daily(*, start: str = "2015-01-01") -> tuple[pd.DataFrame, dict[str, Any]]:
    """全市场 + 科创板日频实际/计划解禁市值。"""
    sess = _session()
    frames: list[pd.DataFrame] = []
    detail: dict[str, Any] = {"source": "eastmoney.RPT_LIFTDAY_STA", "ok": False}
    for universe in ("all", "star"):
        frame, meta = fetch_universe_daily(universe, start=start, session=sess)
        detail[universe] = meta
        if not frame.empty:
            frames.append(frame)
    if not frames:
        detail["error"] = (detail.get("all") or {}).get("error") or "empty"
        return pd.DataFrame(columns=["date", "universe", "amount_yi", "plan_yi", "stock_n", "source"]), detail
    out = pd.concat(frames, ignore_index=True)
    detail["ok"] = bool((detail.get("all") or {}).get("ok"))
    detail["rows"] = int(len(out))
    return out, detail


def _naive(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = pd.Timestamp(stamp.to_pydatetime().replace(tzinfo=None))
    return stamp.normalize()


def _period_end(ts: pd.Timestamp, freq: str) -> pd.Timestamp:
    stamp = _naive(ts)
    if freq == "QE":
        return _naive(stamp.to_period("Q").end_time)
    return _naive(stamp.to_period("M").end_time)


def _period_start(period_end: pd.Timestamp, freq: str) -> pd.Timestamp:
    end = _naive(period_end)
    if freq == "QE":
        return _naive(end.to_period("Q").start_time)
    return end.replace(day=1)


# 解禁是排期，比周 K 早知道。图上多放大约半年，方便看后面的供给。
UNLOCK_FORWARD_DAYS = 183


def extend_weekly_anchors(
    anchors: pd.DatetimeIndex | None,
    forward_days: int = UNLOCK_FORWARD_DAYS,
) -> pd.DatetimeIndex:
    """周 K 最后一个周五之后，按周五补到大约半年后的月末。"""
    if anchors is None or len(anchors) == 0:
        return pd.DatetimeIndex([])
    raw = pd.to_datetime(pd.Index(anchors))
    if getattr(raw, "tz", None) is not None:
        raw = raw.tz_convert("UTC").tz_localize(None)
    anc = pd.DatetimeIndex(raw).normalize().unique().sort_values()
    if len(anc) == 0:
        return anc
    last = pd.Timestamp(anc[-1]).normalize()
    until = _naive((last + pd.Timedelta(days=forward_days)).to_period("M").end_time).normalize()
    extra = pd.date_range(last + pd.Timedelta(days=7), until, freq="W-FRI")
    if len(extra) == 0:
        return anc
    return anc.append(pd.DatetimeIndex(extra)).unique().sort_values()


def aggregate_unlock_periods(
    daily: pd.DataFrame,
    anchors: pd.DatetimeIndex | None,
    *,
    freq: str,
    snap_days: int = 20,
    forward_days: int = UNLOCK_FORWARD_DAYS,
) -> pd.DataFrame:
    """日频实际解禁市值 → 月或季，时间落到周 K 或其后的周五。

    周 K 之后大约半年内的已排期照样画。再远的不接上来。
    """
    empty = pd.DataFrame(columns=["date", "全市场_亿元", "科创板_亿元"])
    if daily is None or daily.empty or anchors is None or len(anchors) == 0:
        return empty
    anc = extend_weekly_anchors(anchors, forward_days)
    if len(anc) == 0:
        return empty
    last = pd.Timestamp(anc[-1]).normalize()
    horizon = _naive(last.to_period("M").end_time).normalize()

    x = daily.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce").dt.normalize()
    x["amount_yi"] = pd.to_numeric(x.get("amount_yi"), errors="coerce")
    x["universe"] = x.get("universe").astype(str)
    x = x.dropna(subset=["date", "amount_yi"])
    x = x.loc[x["date"] <= horizon]
    if x.empty:
        return empty

    parts: list[pd.Series] = []
    for universe, col in (("all", "全市场_亿元"), ("star", "科创板_亿元")):
        sub = x.loc[x["universe"] == universe].set_index("date").sort_index()
        if sub.empty:
            continue
        rule = "QE" if freq == "QE" else "ME"
        series = sub["amount_yi"].resample(rule).sum(min_count=1).dropna()
        parts.append(series.rename(col))
    if not parts:
        return empty
    wide = pd.concat(parts, axis=1)
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index)).normalize()

    snapped: dict[pd.Timestamp, dict[str, float]] = {}
    for period_end, row in wide.iterrows():
        end = pd.Timestamp(period_end).normalize()
        pos = int(anc.searchsorted(end))
        cands: list[pd.Timestamp] = []
        if pos < len(anc):
            cands.append(pd.Timestamp(anc[pos]))
        if pos > 0:
            cands.append(pd.Timestamp(anc[pos - 1]))
        before = [item for item in cands if item <= end and abs(item - end) <= pd.Timedelta(days=snap_days)]
        if before:
            best = max(before)
        else:
            best = min(cands, key=lambda item: abs(item - end)) if cands else None
        if best is None or abs(best - end) > pd.Timedelta(days=snap_days):
            continue
        bucket = snapped.setdefault(best, {})
        for col in ("全市场_亿元", "科创板_亿元"):
            if col in row and pd.notna(row[col]):
                bucket[col] = float(bucket.get(col, 0.0)) + float(row[col])

    if not snapped:
        return empty
    records = []
    for day in sorted(snapped):
        rec = snapped[day]
        records.append(
            {
                "date": day,
                "全市场_亿元": rec.get("全市场_亿元"),
                "科创板_亿元": rec.get("科创板_亿元"),
            }
        )
    return pd.DataFrame(records)


def cross_check_detail_day(day: str) -> dict[str, Any]:
    """用解禁明细（另一张报表）加总，核对日统计。失败不抛。"""
    meta: dict[str, Any] = {"id": "unlock_live", "day": day, "ok": False, "error": None}
    sess = _session()
    try:
        summary_all, all_meta = fetch_universe_daily("all", start=day, end=day, session=sess)
        summary_star, star_meta = fetch_universe_daily("star", start=day, end=day, session=sess)
        # fetch_universe_daily 从 start 拉到未来，这里只留当天
        day_ts = pd.Timestamp(day)
        summary_all = summary_all.loc[summary_all["date"] == day_ts] if not summary_all.empty else summary_all
        summary_star = summary_star.loc[summary_star["date"] == day_ts] if not summary_star.empty else summary_star
        if summary_all.empty:
            meta["error"] = all_meta.get("error") or "summary empty"
            return meta

        filt = f"(FREE_DATE='{day[:10]}')"
        params = {
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
            "pageSize": "500",
            "pageNumber": "1",
            "reportName": "RPT_LIFT_STAGE",
            "columns": "SECURITY_CODE,FREE_DATE,LIFT_MARKET_CAP",
            "source": "WEB",
            "client": "WEB",
            "filter": filt,
        }
        first = _get_page(sess, params)
        pages = int(first.get("pages") or 1)
        rows = list(first.get("data") or [])
        for page in range(2, pages + 1):
            params["pageNumber"] = str(page)
            rows.extend((_get_page(sess, params).get("data") or []))
        detail_all = 0.0
        detail_star = 0.0
        for row in rows:
            val = pd.to_numeric(row.get("LIFT_MARKET_CAP"), errors="coerce")
            if pd.isna(val):
                continue
            yi = float(val) / 10000.0
            detail_all += yi
            if str(row.get("SECURITY_CODE") or "").startswith("688"):
                detail_star += yi
        sum_all = float(summary_all["amount_yi"].iloc[0])
        sum_star = float(summary_star["amount_yi"].iloc[0]) if not summary_star.empty else 0.0
        meta.update(
            {
                "ok": True,
                "summary_all_yi": sum_all,
                "detail_all_yi": detail_all,
                "summary_star_yi": sum_star,
                "detail_star_yi": detail_star,
                "detail_n": len(rows),
                "star_fetch_ok": bool(star_meta.get("ok") or not summary_star.empty),
            }
        )
        return meta
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return meta
