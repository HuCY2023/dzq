# -*- coding: utf-8 -*-
"""03 杠杆资金：沪深融资余额合计、多源交叉验证、月变化、历史分位。

主存口径：沪+深「融资余额」合计（亿元）。不强制绑定单一商业终端；
入库时用交易所汇总 / 东财市场表做交叉验证，稳定性写入 meta。

分板（副图叠加，不影响打分主序列）：
  按个股明细代码段汇总 → 沪主板 / 深主板 / 创业板 / 科创板（原子列），
  展示层再合成「主板」(沪+深主板)、「深市」(深主板+创业)。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Any, Iterable

import pandas as pd

from data_source.dp_cycle._http import call_with_retry, ensure_http

logger = logging.getLogger(__name__)

# 近端交叉验证：相对偏差超过该阈值则记 warn（不阻断入库）
_XCHECK_REL_WARN_PCT = 1.0
_XCHECK_RECENT_DAYS = 60

# 展示层四板（与用户口径一致；主板/深市与创业有重叠属预期）
BOARD_DISPLAY = ("主板", "深市", "科创板", "创业板")


def _to_yi(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    med = float(s.dropna().median()) if s.notna().any() else 0.0
    if med > 1e6:
        return s / 1e8
    return s


def _norm_exchange_pair(df: pd.DataFrame, tag: str) -> pd.DataFrame:
    out = df.copy()
    date_col = "日期" if "日期" in out.columns else out.columns[0]
    out["date"] = pd.to_datetime(out[date_col], errors="coerce").dt.normalize()
    if "融资余额" not in out.columns:
        raise ValueError(f"{tag} 缺融资余额列: {list(out.columns)}")
    out[f"rz_{tag}"] = _to_yi(out["融资余额"])
    if "融资融券余额" in out.columns:
        out[f"rzrq_{tag}"] = _to_yi(out["融资融券余额"])
    else:
        out[f"rzrq_{tag}"] = float("nan")
    return out[["date", f"rz_{tag}", f"rzrq_{tag}"]].dropna(subset=["date"])


def _fetch_macro_sh_sz() -> tuple[pd.DataFrame, dict[str, Any]]:
    """AkShare 宏观表：沪 + 深（历史最长，作主存）。"""
    import akshare as ak

    meta: dict[str, Any] = {"id": "macro_sh_sz", "ok": False, "error": None, "sources": {}}
    frames = []
    for tag, fetch in (("sh", ak.macro_china_market_margin_sh), ("sz", ak.macro_china_market_margin_sz)):
        source: dict[str, Any] = {"ok": False, "rows": 0, "error": None}
        try:
            frame = _norm_exchange_pair(call_with_retry(fetch), tag)
            source["rows"] = int(len(frame))
            source["ok"] = bool(frame[f"rz_{tag}"].notna().any())
            source["last_date"] = str(frame["date"].max().date()) if len(frame) else None
            if not source["ok"]:
                source["error"] = "empty"
        except Exception as exc:  # noqa: BLE001
            source["error"] = f"{type(exc).__name__}: {exc}"
            frame = pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]"),
                                  f"rz_{tag}": pd.Series(dtype=float),
                                  f"rzrq_{tag}": pd.Series(dtype=float)})
        meta["sources"][tag] = source
        frames.append(frame)
    out = pd.merge(frames[0], frames[1], on="date", how="outer").sort_values("date")
    out["融资余额_亿元"] = out["rz_sh"] + out["rz_sz"]
    out["两融余额_亿元"] = out["rzrq_sh"] + out["rzrq_sz"]
    valid = out.dropna(subset=["融资余额_亿元"])
    partial = bool(len(out) and out[["融资余额_亿元", "两融余额_亿元"]].isna().any(axis=None))
    errors = [f"{tag}: {source['error']}" for tag, source in meta["sources"].items() if source["error"]]
    meta.update({
        "ok": bool(len(out) and not partial and not errors),
        "partial": partial,
        "error": "; ".join(errors) if errors else None,
        "rows": int(len(out)),
        "complete_rows": int(out[["融资余额_亿元", "两融余额_亿元"]].notna().all(axis=1).sum()),
        "first_date": str(valid["date"].iloc[0].date()) if len(valid) else None,
        "last_date": str(valid["date"].iloc[-1].date()) if len(valid) else None,
        "latest_raw_date": str(out["date"].iloc[-1].date()) if len(out) else None,
    })
    return out.reset_index(drop=True), meta


def _fetch_sse_financing() -> tuple[pd.DataFrame, dict[str, Any]]:
    """上交所官方汇总（与 macro_sh 应对齐；用于交叉验证，非强制主存）。"""
    import akshare as ak

    meta: dict[str, Any] = {"id": "sse", "ok": False, "error": None}
    try:
        raw = call_with_retry(
            ak.stock_margin_sse,
            start_date="20150101",
            end_date=pd.Timestamp.today().strftime("%Y%m%d"),
        )
        out = raw.copy()
        out["date"] = pd.to_datetime(out["信用交易日期"], errors="coerce").dt.normalize()
        out["融资余额_亿元"] = _to_yi(out["融资余额"])
        if "融资融券余额" in out.columns:
            out["两融余额_亿元"] = _to_yi(out["融资融券余额"])
        else:
            out["两融余额_亿元"] = out["融资余额_亿元"]
        out = out[["date", "融资余额_亿元", "两融余额_亿元"]].dropna(subset=["date", "融资余额_亿元"])
        meta.update({"ok": True, "rows": int(len(out))})
        if len(out):
            meta["last_date"] = str(out["date"].iloc[-1].date())
        return out.reset_index(drop=True), meta
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return pd.DataFrame(columns=["date", "融资余额_亿元", "两融余额_亿元"]), meta


def _em_page(page: int, page_size: int = 500) -> list[dict[str, Any]]:
    """拉东财一页；失败抛错供 call_with_retry。"""
    qs = urllib.parse.urlencode(
        {
            "reportName": "RPTA_RZRQ_LSHJ",
            "columns": "ALL",
            "source": "WEB",
            "pageNumber": str(page),
            "pageSize": str(page_size),
            "sortColumns": "DIM_DATE",
            "sortTypes": "-1",
        }
    )
    url = f"https://datacenter-web.eastmoney.com/api/data/v1/get?{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/rzrq/",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8", "ignore"))
    if not payload.get("success"):
        raise RuntimeError(payload.get("message") or "em api failed")
    return list(((payload.get("result") or {}).get("data")) or [])


def _fetch_em_market(*, max_pages: int = 3) -> tuple[pd.DataFrame, dict[str, Any]]:
    """东财市场两融（RPTA_RZRQ_LSHJ）。交叉验证只需近端，默认少拉几页。"""
    meta: dict[str, Any] = {"id": "em_rpta_rzrq_lshj", "ok": False, "error": None}
    ensure_http()
    rows: list[dict[str, Any]] = []
    try:
        for page in range(1, max_pages + 1):
            chunk = call_with_retry(_em_page, page, retries=3, delay=1.0)
            if not chunk:
                break
            rows.extend(chunk)
        if not rows:
            meta["error"] = "empty"
            return pd.DataFrame(columns=["date", "融资余额_亿元", "两融余额_亿元"]), meta
        df = pd.DataFrame(rows)
        if "DIM_DATE" not in df.columns or "RZYE" not in df.columns:
            raise RuntimeError(f"unexpected cols: {list(df.columns)[:12]}")
        df["date"] = pd.to_datetime(df["DIM_DATE"], errors="coerce").dt.normalize()
        df["融资余额_亿元"] = _to_yi(df["RZYE"])
        if "RZRQYE" in df.columns:
            df["两融余额_亿元"] = _to_yi(df["RZRQYE"])
        else:
            df["两融余额_亿元"] = df["融资余额_亿元"]
        out = (
            df[["date", "融资余额_亿元", "两融余额_亿元"]]
            .dropna(subset=["date", "融资余额_亿元"])
            .drop_duplicates("date")
            .sort_values("date")
        )
        meta.update(
            {
                "ok": True,
                "rows": int(len(out)),
                "last_date": str(out["date"].iloc[-1].date()) if len(out) else None,
            }
        )
        return out.reset_index(drop=True), meta
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return pd.DataFrame(columns=["date", "融资余额_亿元", "两融余额_亿元"]), meta


def _spot_szse(date: pd.Timestamp) -> dict[str, Any]:
    """深交所单日汇总点验。"""
    import akshare as ak

    meta: dict[str, Any] = {"id": "szse_spot", "ok": False, "date": str(date.date())}
    try:
        raw = call_with_retry(ak.stock_margin_szse, date=date.strftime("%Y%m%d"))
        if raw is None or raw.empty:
            meta["error"] = "empty"
            return meta
        row = raw.iloc[0]
        # 深交所接口单位已是亿元量级
        rz = float(pd.to_numeric(row.get("融资余额"), errors="coerce"))
        meta.update({"ok": True, "融资余额_亿元": rz})
        if "融资融券余额" in raw.columns:
            meta["两融余额_亿元"] = float(pd.to_numeric(row.get("融资融券余额"), errors="coerce"))
        return meta
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return meta


def _compare_series(
    primary: pd.DataFrame,
    other: pd.DataFrame,
    *,
    name: str,
    recent_days: int = _XCHECK_RECENT_DAYS,
) -> dict[str, Any]:
    """对比融资余额；返回 overlap / 相对偏差统计。"""
    out: dict[str, Any] = {"vs": name, "ok": False}
    if primary.empty or other.empty:
        out["error"] = "empty"
        return out
    a = primary[["date", "融资余额_亿元"]].rename(columns={"融资余额_亿元": "p"})
    b = other[["date", "融资余额_亿元"]].rename(columns={"融资余额_亿元": "q"})
    m = a.merge(b, on="date", how="inner").dropna()
    if m.empty:
        out["error"] = "no_overlap"
        return out
    m["rel_pct"] = (m["p"] - m["q"]) / m["q"].replace(0, pd.NA) * 100.0
    m = m.dropna(subset=["rel_pct"])
    recent = m.tail(min(recent_days, len(m)))
    out.update(
        {
            "ok": True,
            "overlap": int(len(m)),
            "abs_rel_mean_pct": float(m["rel_pct"].abs().mean()),
            "abs_rel_max_pct": float(m["rel_pct"].abs().max()),
            "within_0_5pct": float((m["rel_pct"].abs() <= 0.5).mean() * 100.0),
            "recent_n": int(len(recent)),
            "recent_abs_rel_mean_pct": float(recent["rel_pct"].abs().mean()),
            "recent_abs_rel_max_pct": float(recent["rel_pct"].abs().max()),
            "recent_within_1pct": float((recent["rel_pct"].abs() <= 1.0).mean() * 100.0),
            "last_primary": float(m["p"].iloc[-1]),
            "last_other": float(m["q"].iloc[-1]),
            "last_rel_pct": float(m["rel_pct"].iloc[-1]),
        }
    )
    out["warn"] = bool(out["recent_abs_rel_max_pct"] > _XCHECK_REL_WARN_PCT)
    return out


def cross_validate_margin(primary: pd.DataFrame) -> dict[str, Any]:
    """多源交叉验证（不改主存序列，只出报告）。"""
    report: dict[str, Any] = {"checks": [], "stable": None, "notes": []}
    if primary.empty:
        report["stable"] = False
        report["notes"].append("primary empty")
        return report

    sse_df, sse_meta = _fetch_sse_financing()
    report["sources"] = {"sse": sse_meta}
    # 用 macro 的沪市子集 vs SSE：这里对比的是「合计 vs 沪市」无意义；
    # 正确做法：SSE 只核沪市。从 primary 无法拆沪市时，改为：
    # 拉 macro_sh 单边再比 —— 已在 fetch 流程外用交易所对宏观点验。
    # 实际：SSE 融资余额应等于 macro 沪市；这里用「合计」对比会差一个深市。
    # 因此改为：内部再取 macro_sh 与 SSE 比。
    try:
        import akshare as ak

        sh = call_with_retry(ak.macro_china_market_margin_sh)
        sh_raw = _norm_exchange_pair(sh, "sh")
        sh_n = pd.DataFrame(
            {
                "date": sh_raw["date"],
                "融资余额_亿元": sh_raw["rz_sh"],
                "两融余额_亿元": sh_raw["rzrq_sh"],
            }
        )
        c_sse = _compare_series(sh_n, sse_df, name="macro_sh_vs_sse")
    except Exception as exc:  # noqa: BLE001
        c_sse = {"vs": "macro_sh_vs_sse", "ok": False, "error": str(exc)}
    report["checks"].append(c_sse)

    em_df, em_meta = _fetch_em_market()
    report["sources"]["em"] = em_meta
    c_em = _compare_series(primary, em_df, name="macro_total_vs_em")
    report["checks"].append(c_em)

    last = pd.to_datetime(primary["date"].iloc[-1])
    # 深市点验：用合计 − 若有 SSE 则与 (合计−沪) 对照；直接点验 szse
    sz_spot = _spot_szse(last)
    report["sources"]["szse_spot"] = sz_spot
    if sz_spot.get("ok") and sse_df is not None and not sse_df.empty:
        sse_last = sse_df.loc[sse_df["date"] == last]
        if len(sse_last):
            sh_yi = float(sse_last["融资余额_亿元"].iloc[0])
            sz_yi = float(sz_spot["融资余额_亿元"])
            tot = float(primary.loc[primary["date"] == last, "融资余额_亿元"].iloc[0])
            recon = sh_yi + sz_yi
            rel = abs(tot - recon) / recon * 100.0 if recon else None
            spot = {
                "vs": "macro_total_vs_sse_plus_szse",
                "ok": True,
                "last_macro": tot,
                "last_sse_plus_szse": recon,
                "last_rel_pct": rel,
                "warn": bool(rel is not None and rel > _XCHECK_REL_WARN_PCT),
            }
            report["checks"].append(spot)

    warns = [c for c in report["checks"] if c.get("warn")]
    # 稳定门槛：沪市 macro≡SSE；且（东财近端合格 或 沪+深点验合格）
    sse_ok = bool(
        c_sse.get("ok") and not c_sse.get("warn") and c_sse.get("recent_within_1pct", 0) >= 99
    )
    em_recent_ok = bool(c_em.get("ok") and c_em.get("recent_within_1pct", 0) >= 90)
    spot = next((c for c in report["checks"] if c.get("vs") == "macro_total_vs_sse_plus_szse"), None)
    spot_ok = bool(spot and spot.get("ok") and not spot.get("warn"))
    report["stable"] = bool(sse_ok and (em_recent_ok or spot_ok))
    if sse_ok:
        report["notes"].append("macro_sh ≡ SSE（近端相对偏差 <1%）")
    if spot_ok:
        report["notes"].append(
            f"最新日 macro合计 ≡ SSE+深交所点验（rel={spot.get('last_rel_pct'):.4g}%）"
        )
    if c_em.get("ok"):
        report["notes"].append(
            f"macro合计 vs 东财：近{_XCHECK_RECENT_DAYS}日 |rel| mean="
            f"{c_em.get('recent_abs_rel_mean_pct'):.3f}% max={c_em.get('recent_abs_rel_max_pct'):.3f}%"
        )
    elif c_em.get("error"):
        report["notes"].append(f"东财交叉验证暂不可用: {c_em.get('error')}")
    if warns:
        report["notes"].append(f"warn checks: {[w.get('vs') for w in warns]}")
    return report


def fetch_margin_daily(*, cross_check: bool = True) -> tuple[pd.DataFrame, dict[str, Any]]:
    """返回日度融资余额（亿元）与元数据；默认做多源交叉验证。"""
    out, macro_meta = _fetch_macro_sh_sz()
    meta: dict[str, Any] = {
        "source": "macro_china_market_margin_sh+sz",
        "ok": macro_meta.get("ok", False),
        "partial": macro_meta.get("partial", False),
        "error": macro_meta.get("error"),
        "primary": macro_meta,
        "last_date": macro_meta.get("last_date"),
        "latest_raw_date": macro_meta.get("latest_raw_date"),
    }
    if not out.empty:
        meta["rows"] = int(len(out))
        value = out["融资余额_亿元"].iloc[-1]
        meta["last_融资余额_亿元"] = float(value) if pd.notna(value) else None
    complete = out.dropna(subset=["融资余额_亿元"])
    if cross_check and not complete.empty:
        try:
            meta["cross_check"] = cross_validate_margin(complete)
            meta["cross_check_stable"] = meta["cross_check"].get("stable")
        except Exception as exc:  # noqa: BLE001
            meta["cross_check"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            meta["cross_check_stable"] = False
    return out.reset_index(drop=True) if len(out) else out, meta


def _margin_local_timestamp(value: Any) -> pd.Timestamp:
    """将带时区时间转为台北本地时间；无时区时间本身就是本地时间。"""
    stamp = pd.Timestamp(value)
    if pd.notna(stamp) and stamp.tzinfo is not None:
        stamp = stamp.tz_convert("Asia/Taipei").tz_localize(None)
    return stamp


def _margin_local_timestamps(values: pd.Series) -> pd.Series:
    """按本地时间比较可混合日期、时间及带时区的发布时间。"""
    if isinstance(values.dtype, pd.DatetimeTZDtype):
        return values.dt.tz_convert("Asia/Taipei").dt.tz_localize(None)
    if pd.api.types.is_datetime64_any_dtype(values.dtype):
        return values

    def convert(value: Any) -> pd.Timestamp:
        try:
            return _margin_local_timestamp(value)
        except (TypeError, ValueError, OverflowError):
            return pd.NaT

    return pd.to_datetime(values.map(convert), errors="coerce")


def causal_margin_frame(
    df: pd.DataFrame | None, *, as_of: Any | None = None, trade_calendar: dict[str, Any] | None = None
) -> pd.DataFrame:
    """按当期可见余额计算展开分位及 20 个交易日或观测日间隔的变化。"""
    if df is None or df.empty or not {"date", "融资余额_亿元"}.issubset(df.columns):
        return pd.DataFrame()
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce").dt.normalize()
    x["融资余额_亿元"] = pd.to_numeric(x["融资余额_亿元"], errors="coerce")
    x = x.dropna(subset=["date"])
    if "available_at" in x.columns:
        x["available_at"] = _margin_local_timestamps(x["available_at"])
    if as_of is not None:
        cutoff = _margin_local_timestamp(as_of)
        x = x.loc[x["date"] <= cutoff.normalize()]
        if "available_at" in x.columns:
            released = x["available_at"]
            missing = x["融资余额_亿元"].isna()
            x = x.loc[(released <= cutoff) | (released.isna() & missing)]
    if "available_at" in x.columns:
        x = x.sort_values("available_at", kind="stable", na_position="first")
    x = x.sort_values("date", kind="stable").drop_duplicates("date", keep="last").reset_index(drop=True)
    positions = None
    if not x.empty and isinstance(trade_calendar, dict):
        start = pd.to_datetime(trade_calendar.get("coverage_start"), errors="coerce")
        end = pd.to_datetime(trade_calendar.get("coverage_end"), errors="coerce")
        if pd.notna(start) and pd.notna(end):
            days = pd.DatetimeIndex(pd.to_datetime(trade_calendar.get("dates", []), errors="coerce")).dropna().normalize()
            days = days[(days >= start) & (days <= end)].sort_values().unique()
            positions = days.get_indexer(pd.DatetimeIndex(x["date"]))
    balances = x["融资余额_亿元"]
    for interval in (5, 20):
        delta = balances - balances.shift(interval)
        verified = pd.Series(False, index=x.index)
        if positions is not None:
            verified = pd.Series(positions >= interval, index=x.index)
            if verified.any():
                prior_dates = days.take(positions[verified.to_numpy()] - interval)
                prior = x.set_index("date")["融资余额_亿元"].reindex(prior_dates).to_numpy()
                delta.loc[verified] = balances.loc[verified].to_numpy() - prior
        x[f"融资_{interval}交易日变化_亿元"] = delta
        x[f"融资_{interval}交易日历已核验"] = verified
    x["融资_截至当期分位"] = balances.expanding(min_periods=1).rank(pct=True) * 100.0
    x["融资_有效样本数"] = balances.notna().cumsum()
    x["融资_交易日历已核验"] = x["融资_20交易日历已核验"]
    return x


def margin_signal_from_row(row: pd.Series | None) -> dict[str, Any]:
    """用一个已计算的当期融资观测形成与展示层共用的信号。"""
    if row is None:
        return {"status": "gap", "reason": "无融资余额"}
    if pd.isna(row["融资余额_亿元"]):
        return {"status": "partial", "reason": f"{pd.Timestamp(row['date']).date()} 融资余额缺失"}
    if row["融资_有效样本数"] < 25 or pd.isna(row["融资_20交易日变化_亿元"]):
        return {"status": "gap", "reason": "融资余额样本不足或 20 个交易日间隔缺失"}
    from data_source.dp_cycle.rules import score_margin_regime

    pct = float(row["融资_截至当期分位"])
    delta = float(row["融资_20交易日变化_亿元"])
    delta_5 = row["融资_5交易日变化_亿元"]
    score, zone, trend, tag = score_margin_regime(pct, delta)
    return {"status": "ok", "date": pd.Timestamp(row["date"]), "balance": float(row["融资余额_亿元"]),
            "pct": pct, "delta": delta, "delta_5": float(delta_5) if pd.notna(delta_5) else None,
            "calendar_verified_5": bool(row["融资_5交易日历已核验"]),
            "score": score, "zone": zone, "trend": trend, "tag": tag,
            "calendar_verified": bool(row["融资_交易日历已核验"])}


def margin_signal_at(
    df: pd.DataFrame | None, *, as_of: Any | None = None, trade_calendar: dict[str, Any] | None = None
) -> dict[str, Any]:
    x = causal_margin_frame(df, as_of=as_of, trade_calendar=trade_calendar)
    return margin_signal_from_row(x.iloc[-1] if not x.empty else None)


def enrich_margin(df: pd.DataFrame, *, lookback_years: int = 5) -> pd.DataFrame:
    """月末值月变化、截至当期的展开分位、近 N 年分位。"""
    if df.empty:
        return df
    x = causal_margin_frame(df)
    x["分位_全样本"] = x["融资_截至当期分位"]

    # ── 性能优化：numpy searchsorted 替代 iterrows + .loc[] 切片 ──
    import numpy as np
    _vals = x["融资余额_亿元"].to_numpy(dtype=float)
    _dt64 = x["date"].to_numpy(dtype="datetime64[ns]")
    _offset_ns = np.timedelta64(lookback_years * 365, "D")  # 近似 N 年
    _left_idx = np.searchsorted(_dt64, _dt64 - _offset_ns, side="left")
    _n = len(_vals)
    pct_n = np.full(_n, np.nan)
    for i in range(_n):
        li = int(_left_idx[i])
        win = _vals[li : i + 1]
        win = win[~np.isnan(win)]
        if len(win) >= 5 and not np.isnan(_vals[i]):
            pct_n[i] = float((win <= _vals[i]).sum() / len(win) * 100.0)
    x["分位_近N年"] = pct_n
    x["lookback_years"] = lookback_years

    x["month"] = x["date"].dt.to_period("M")
    monthly = x.groupby("month", as_index=False).tail(1).copy()
    monthly["月变化_亿元"] = monthly["融资余额_亿元"].diff()
    x = x.merge(monthly[["date", "月变化_亿元"]], on="date", how="left")
    return x


def classify_margin_code(code: Any) -> str:
    """个股代码 → 板：sh_main / sz_main / chinext / star / other（ETF/债等）。"""
    c = str(code or "").split(".")[0].strip().zfill(6)
    if not c.isdigit():
        return "other"
    if c.startswith("688"):
        return "star"
    if c.startswith(("300", "301")):
        return "chinext"
    if c.startswith("60"):
        return "sh_main"
    if c.startswith(("000", "001", "002", "003")):
        return "sz_main"
    return "other"


def aggregate_margin_boards_from_details(
    sse: pd.DataFrame | None, szse: pd.DataFrame | None, *, day: pd.Timestamp
) -> dict[str, Any]:
    """把沪/深个股明细汇总成板余额（亿元）。不含 ETF 等 other。"""
    buckets = {key: float("nan") for key in ("sh_main", "sz_main", "chinext", "star")}
    counts = {"sse": 0, "szse": 0}
    for name, data, code_name, code_index, keys in (
        ("sse", sse, "标的证券代码", 1, ("sh_main", "star")),
        ("szse", szse, "证券代码", 0, ("sz_main", "chinext")),
    ):
        if data is None or data.empty or "融资余额" not in data.columns:
            continue
        if code_name in data.columns:
            code_col = code_name
        elif len(data.columns) > code_index:
            code_col = data.columns[code_index]
        else:
            continue
        codes = data[code_col].map(classify_margin_code)
        ye = pd.to_numeric(data["融资余额"], errors="coerce")
        if not ye.notna().any():
            continue
        counts[name] = int(len(data))
        for key in keys:
            values = ye[codes == key]
            buckets[key] = float(values.sum(min_count=len(values))) if len(values) else 0.0
    # 交易所明细单位为元
    sh_m = buckets["sh_main"] / 1e8
    sz_m = buckets["sz_main"] / 1e8
    cx = buckets["chinext"] / 1e8
    star = buckets["star"] / 1e8
    return {
        "date": pd.Timestamp(day).normalize(),
        "沪主板_亿元": sh_m,
        "深主板_亿元": sz_m,
        "创业板_亿元": cx,
        "科创板_亿元": star,
        "主板_亿元": sh_m + sz_m,
        "深市_亿元": sz_m + cx,
        "_detail_n": counts["sse"] + counts["szse"],
        "_sse_n": counts["sse"],
        "_szse_n": counts["szse"],
    }


def fetch_margin_boards_one_day(day: pd.Timestamp | str) -> dict[str, Any]:
    """单日：AkShare 沪/深融资融券明细 → 分板。"""
    import akshare as ak

    ts = pd.Timestamp(day).normalize()
    d = ts.strftime("%Y%m%d")
    errors = {}
    try:
        sse = call_with_retry(ak.stock_margin_detail_sse, date=d, retries=2, delay=1.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("margin boards SSE %s: %s", d, exc)
        errors["sse"] = f"{type(exc).__name__}: {exc}"
        sse = pd.DataFrame()
    try:
        szse = call_with_retry(ak.stock_margin_detail_szse, date=d, retries=2, delay=1.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("margin boards SZSE %s: %s", d, exc)
        errors["szse"] = f"{type(exc).__name__}: {exc}"
        szse = pd.DataFrame()
    row = aggregate_margin_boards_from_details(sse, szse, day=ts)
    for name, columns in (("sse", ("沪主板_亿元", "科创板_亿元")),
                          ("szse", ("深主板_亿元", "创业板_亿元"))):
        if any(pd.isna(row[column]) for column in columns) and name not in errors:
            errors[name] = "incomplete financing data" if row[f"_{name}_n"] else "empty or missing financing data"
    atomic = [row[name] for name in ("沪主板_亿元", "深主板_亿元", "创业板_亿元", "科创板_亿元")]
    row["ok"] = all(pd.notna(value) for value in atomic)
    row["partial"] = any(pd.notna(value) for value in atomic) and not row["ok"]
    row["errors"] = errors
    return row


def select_board_fetch_dates(
    margin_dates: Iterable,
    have_dates: Iterable,
    *,
    max_days: int = 120,
    recent_calendar_days: int = 90,
) -> list[pd.Timestamp]:
    """缺板日期：近端全日补齐，更早只取周五（对齐周图）；最新优先。"""
    if max_days <= 0:
        return []
    md = sorted({pd.Timestamp(d).normalize() for d in margin_dates if pd.notna(d)})
    if not md:
        return []
    have = {pd.Timestamp(d).normalize() for d in have_dates if pd.notna(d)}
    missing = [d for d in md if d not in have]
    if not missing:
        return []
    cutoff = md[-1] - pd.Timedelta(days=int(recent_calendar_days))
    recent = [d for d in missing if d >= cutoff]
    older_fri = [d for d in missing if d < cutoff and int(d.weekday()) == 4]
    ordered = sorted(recent, reverse=True) + sorted(older_fri, reverse=True)
    return ordered[: int(max_days)]


def fetch_margin_boards_for_dates(
    dates: Iterable,
    *,
    sleep_s: float = 0.35,
    progress_cb: Any | None = None,
) -> pd.DataFrame:
    """批量拉分板；保留部分结果和失败信息，缺失列下次继续补。"""
    cols = [
        "date",
        "沪主板_亿元",
        "深主板_亿元",
        "创业板_亿元",
        "科创板_亿元",
        "主板_亿元",
        "深市_亿元",
        "_detail_n",
        "_sse_n",
        "_szse_n",
        "ok",
        "partial",
        "errors",
    ]
    rows: list[dict[str, Any]] = []
    uniq = sorted({pd.Timestamp(d).normalize() for d in dates if pd.notna(d)})
    total = len(uniq)
    for i, day in enumerate(uniq):
        if progress_cb is not None:
            try:
                progress_cb(
                    i + 1,
                    max(total, 1),
                    f"融资分板 {day.date()} ({i + 1}/{total})",
                )
            except Exception:
                pass
        row = fetch_margin_boards_one_day(day)
        rows.append({k: row[k] for k in cols})
        if sleep_s > 0 and i + 1 < len(uniq):
            time.sleep(sleep_s)
    if not rows:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(rows).drop_duplicates("date").sort_values("date")
    return out.reset_index(drop=True)


def attach_display_boards(df: pd.DataFrame) -> pd.DataFrame:
    """若仅有原子列，补主板/深市展示列。"""
    if df is None or df.empty:
        return df
    x = df.copy()
    if "沪主板_亿元" in x.columns and "深主板_亿元" in x.columns:
        if "主板_亿元" not in x.columns:
            x["主板_亿元"] = pd.to_numeric(x["沪主板_亿元"], errors="coerce") + pd.to_numeric(
                x["深主板_亿元"], errors="coerce"
            )
    if "深主板_亿元" in x.columns and "创业板_亿元" in x.columns:
        if "深市_亿元" not in x.columns:
            x["深市_亿元"] = pd.to_numeric(x["深主板_亿元"], errors="coerce") + pd.to_numeric(
                x["创业板_亿元"], errors="coerce"
            )
    return x
