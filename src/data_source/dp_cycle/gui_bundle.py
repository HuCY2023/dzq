# -*- coding: utf-8 -*-
"""GUI：周线 + 五项。优先读 DuckDB（2015+），按需更新入库。"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import pandas as pd

from data_source.dp_cycle.store import HISTORY_START_STR, load_from_db, table_counts

DEFAULT_WEEKLY_INDEX = "sh000001"
INDEX_CHOICES = {
    "上证指数": "sh000001",
    "沪深300": "sh000300",
    "创业板指": "sz399006",
    "科创综指": "sh000680",
}


def _daily_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"])
    x = x.set_index("date").sort_index()
    for c in ("open", "high", "low", "close", "volume"):
        if c not in x.columns:
            x[c] = pd.NA
    w = x.resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return w.dropna(subset=["close"]).reset_index()


def fetch_weekly_index(symbol: str = DEFAULT_WEEKLY_INDEX) -> tuple[pd.DataFrame, dict[str, Any]]:
    from data_source.dp_cycle._http import call_with_retry
    from data_source.dp_cycle.store import clip_from_2015

    meta: dict[str, Any] = {
        "source": f"akshare.stock_zh_index_daily({symbol})",
        "ok": False,
        "error": None,
        "symbol": symbol,
    }
    try:
        import akshare as ak

        raw = call_with_retry(ak.stock_zh_index_daily, symbol=symbol)
        daily = raw.copy()
        daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
        for c in ("open", "high", "low", "close", "volume"):
            if c in daily.columns:
                daily[c] = pd.to_numeric(daily[c], errors="coerce")
        weekly = _daily_to_weekly(daily[["date", "open", "high", "low", "close", "volume"]])
        weekly = clip_from_2015(weekly)
        meta["ok"] = True
        meta["weekly_rows"] = int(len(weekly))
        return weekly, meta
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return pd.DataFrame(), meta


def load_dp_cycle_gui_bundle(
    *,
    index_symbol: str = DEFAULT_WEEKLY_INDEX,
    high_low_start: str = HISTORY_START_STR,
    margin_lookback_years: int = 5,
    as_of: date | None = None,
    refresh: bool = False,
    skip_high_low: bool = False,
    mode: str = "auto",
) -> dict[str, Any]:
    """
    refresh=False：只读库（2015+）
    refresh=True：先按颗粒度更新入库，再读库
    """
    del margin_lookback_years  # 入库时用默认；读库不再现算
    meta: dict[str, Any] = {"index_symbol": index_symbol, "history_start": HISTORY_START_STR}

    if refresh:
        from data_source.dp_cycle.ingest import update_dp_cycle_db

        meta["ingest"] = update_dp_cycle_db(
            mode=mode,
            index_symbol=index_symbol,
            high_low_start=high_low_start,
            as_of=as_of,
            skip_high_low=skip_high_low,
        )

    tables = load_from_db(index_symbol=index_symbol, start=HISTORY_START_STR)
    weekly_df = tables.get("weekly")
    if weekly_df is None or getattr(weekly_df, "empty", True):
        from data_source.dp_cycle.store import list_weekly_symbols

        for alt in list_weekly_symbols():
            if alt == index_symbol:
                continue
            alt_tables = load_from_db(index_symbol=alt, start=HISTORY_START_STR)
            if not alt_tables.get("weekly", pd.DataFrame()).empty:
                tables["weekly"] = alt_tables["weekly"]
                meta["index_symbol_requested"] = index_symbol
                meta["index_symbol"] = alt
                meta["weekly_fallback"] = (
                    f"库中无 {index_symbol} 周线，先用 {alt}。"
                    "其他指数请点「更新入库」。"
                )
                index_symbol = alt
                break
    counts = table_counts()
    meta["counts"] = counts
    _wk = tables.get("weekly")
    meta["weekly_rows"] = 0 if _wk is None or getattr(_wk, "empty", True) else int(len(_wk))
    meta["from_db"] = True
    try:
        from data_source.dp_cycle.cross_check import validate_tables

        # 读库：表内快检；更新入库：附带两融多源 + 北向沪深拆分活检
        meta["quality"] = validate_tables(tables, live=bool(refresh))
    except Exception as exc:  # noqa: BLE001
        meta["quality"] = {
            "overall": "warn",
            "summary": f"交叉验证跳过: {type(exc).__name__}",
            "badges": [],
            "error": str(exc),
        }

    # 兼容旧 CSV 聚合字段
    mf = tables.get("main_force_daily", pd.DataFrame())
    if not mf.empty:
        d = mf.copy()
        d["date"] = pd.to_datetime(d["date"])
        weekly_mf = (
            d.set_index("date")[["净额_亿元"]].resample("W-FRI").sum(min_count=1).dropna().reset_index()
        )
        monthly_mf = (
            d.set_index("date")[["净额_亿元"]].resample("ME").sum(min_count=1).dropna().reset_index()
        )
    else:
        weekly_mf = pd.DataFrame()
        monthly_mf = pd.DataFrame()

    etf_hist = tables.get("etf_hist", pd.DataFrame())
    etf_m = pd.DataFrame()
    if not etf_hist.empty:
        from data_source.dp_cycle.national_team_etf import monthly_share_change

        etf_m = monthly_share_change(etf_hist)

    # 各表 ok 标记
    for key, tbl in (
        ("weekly", "weekly"),
        ("01_main_force", "main_force_daily"),
        ("02_national_team", "etf_daily"),
        ("03_margin", "margin"),
        ("04_growth_value", "growth_value"),
        ("05_high_low", "high_low"),
    ):
        df = tables.get(tbl, pd.DataFrame())
        meta[key] = {"ok": not df.empty, "rows": int(len(df))}

    # 美股拥挤度：读库只吃本地快照（无缓存就跳过，绝不在打开软件时扫 FRED/akshare）；
    # 「更新入库」才强制联网重算并写缓存。
    us_crowding: dict[str, Any] | None = None
    try:
        from data_source.dp_cycle.us_crowding import (
            load_cached_crowding_snapshot,
            load_or_compute_crowding_snapshot,
        )

        if refresh:
            us_crowding = load_or_compute_crowding_snapshot(force_refresh=True)
        else:
            us_crowding = load_cached_crowding_snapshot(max_age_hours=24 * 14)
        if us_crowding:
            meta["us_crowding"] = {
                "ok": True,
                "score": us_crowding.get("crowding_score"),
                "verdict": us_crowding.get("verdict"),
                "from_cache": not bool(refresh),
            }
        else:
            meta["us_crowding"] = {
                "ok": False,
                "error": "无本地快照，请点「更新入库」生成" if not refresh else "计算失败",
            }
    except Exception as exc:
        meta["us_crowding"] = {"ok": False, "error": str(exc)}

    # 流动性节点监控数据（按需获取，不阻塞主流程）
    # 在 _switch_view("liquidity") 时单独获取
    liquidity: dict[str, Any] | None = None

    return {
        "weekly": tables.get("weekly", pd.DataFrame()),
        "main_force_daily": mf,
        "main_force_weekly": weekly_mf,
        "main_force_monthly": monthly_mf,
        "northbound_daily": tables.get("northbound_daily", pd.DataFrame()),
        "etf_daily": tables.get("etf_daily", pd.DataFrame()),
        "etf_monthly": etf_m,
        "etf_hist": etf_hist,
        "margin": tables.get("margin", pd.DataFrame()),
        "growth_value": tables.get("growth_value", pd.DataFrame()),
        "high_low": tables.get("high_low", pd.DataFrame()),
        "unlock_daily": tables.get("unlock_daily", pd.DataFrame()),
        "fund_issue": tables.get("fund_issue", pd.DataFrame()),
        "holder_reduce": tables.get("holder_reduce", pd.DataFrame()),
        "industry_flow_daily": tables.get("industry_flow_daily", pd.DataFrame()),
        "us_crowding": us_crowding,
        "liquidity": liquidity,
        "meta": meta,
    }


def _date_str(v: Any) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    ts = pd.to_datetime(v, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d")


def _weekly_anchor_dates(weekly: pd.DataFrame | None) -> pd.DatetimeIndex:
    """与顶图周线同一套周五锚点。"""
    if weekly is None or weekly.empty or "date" not in weekly.columns:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(pd.to_datetime(weekly["date"]).dropna().sort_values().unique())


def _resample_to_weekly(
    df: pd.DataFrame | None,
    value_col: str,
    *,
    how: str = "last",
    anchor: pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
    """日频 → 与顶图同一套 W-FRI。不 ffill，稀疏周只显示有数的那几根。"""
    if df is None or df.empty or value_col not in df.columns:
        return pd.DataFrame(columns=["date", value_col])
    x = df[["date", value_col]].copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce").dt.normalize()
    x[value_col] = pd.to_numeric(x[value_col], errors="coerce")
    x = x.dropna(subset=["date", value_col]).sort_values("date")
    if x.empty:
        return pd.DataFrame(columns=["date", value_col])
    s = x.set_index("date")[value_col]
    if how == "sum":
        w = s.resample("W-FRI").sum(min_count=1)
    elif how == "mean":
        w = s.resample("W-FRI").mean()
    elif how == "max":
        w = s.resample("W-FRI").max()
    else:
        w = s.resample("W-FRI").last()
    w.index = pd.DatetimeIndex(w.index).normalize()
    if anchor is not None and len(anchor):
        # 只保留与周 K 同一周五的点，保证十字线/缩放一一对应
        w = w.reindex(pd.DatetimeIndex(anchor).normalize())
    w = w.dropna()
    out = w.rename(value_col).reset_index()
    out.columns = ["date", value_col]
    return out


def _high_low_captions(hl: pd.DataFrame | None) -> tuple[str, str, str]:
    """返回 (网页短标题, 覆盖说明, 副标题片段)。只描述实际画出的那一种口径。"""
    src = ""
    if hl is not None and not getattr(hl, "empty", True) and "source" in hl.columns:
        picked = hl["source"].dropna()
        if len(picked):
            src = str(picked.iloc[0])
    if src == "lake_52w":
        return (
            "05 新高−新低（右）· 差值（左）· K线湖252日",
            "K线湖约252个交易日新高−新低；未剔ST、已排除北交所；与乐咕120日分库存，不拼接",
            "05 K线湖252日(未剔ST)",
        )
    if src == "legu_120d":
        return (
            "05 新高−新低（右）· 差值（左）· 乐咕120日快径",
            "乐咕120日快径；≠作者52周。湖扫不足时才用这条，不与252日拼接",
            "05乐咕120日快径(≠52周)",
        )
    return (
        "05 新高−新低（右）· 差值（左）",
        "口径未标注",
        "05广度",
    )


def _db_mtime_ns() -> int:
    from data_source.dp_cycle.store import db_path

    path = db_path()
    try:
        return int(os.stat(path).st_mtime_ns)
    except OSError:
        return 0


def _payload_cache_path(index_symbol: str) -> "Path":
    from pathlib import Path

    from output_paths import dp_cycle_store_dir

    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(index_symbol))
    return Path(dp_cycle_store_dir()) / f"gui_payload_{safe}.json"


def load_cached_web_payload(
    index_symbol: str, *, require_heavy: bool = False
) -> dict[str, Any] | None:
    """读 GUI 网页载荷缓存；库文件 mtime 变化则失效。"""
    import json

    path = _payload_cache_path(index_symbol)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        envelope = raw.get("_cache") or {}
        if int(envelope.get("db_mtime_ns") or -1) != _db_mtime_ns():
            return None
        if str(envelope.get("index_symbol") or "") != str(index_symbol):
            return None
        if require_heavy and not bool(envelope.get("heavy", True)):
            return None
        payload = raw.get("payload")
        meta = raw.get("meta") or {}
        if not isinstance(payload, dict):
            return None
        meta = dict(meta)
        meta["from_payload_cache"] = True
        return {"payload": payload, "meta": meta}
    except Exception:
        return None


def save_web_payload_cache(
    index_symbol: str, payload: dict[str, Any], meta: dict[str, Any] | None = None
) -> None:
    import json
    from datetime import datetime

    path = _payload_cache_path(index_symbol)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "_cache": {
                "index_symbol": index_symbol,
                "db_mtime_ns": _db_mtime_ns(),
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "heavy": bool((payload.get("diagnosis") or {}).get("heavy", True)),
            },
            "payload": payload,
            "meta": meta or {},
        }
        path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def invalidate_web_payload_cache(index_symbol: str | None = None) -> None:
    from pathlib import Path

    from output_paths import dp_cycle_store_dir

    root = Path(dp_cycle_store_dir())
    if index_symbol:
        p = _payload_cache_path(index_symbol)
        if p.is_file():
            p.unlink(missing_ok=True)
        return
    for p in root.glob("gui_payload_*.json"):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


def load_gui_web_result(
    *,
    index_symbol: str = DEFAULT_WEEKLY_INDEX,
    high_low_start: str = HISTORY_START_STR,
    refresh: bool = False,
    skip_high_low: bool = False,
    mode: str = "auto",
    heavy: bool | None = None,
) -> dict[str, Any]:
    """GUI 一站式：优先磁盘缓存 → 读库组装。refresh 时强制重算并写缓存。"""
    if heavy is None:
        heavy = bool(refresh)
    if not refresh:
        cached = load_cached_web_payload(index_symbol, require_heavy=bool(heavy))
        if cached is not None:
            return cached

    label = next((k for k, v in INDEX_CHOICES.items() if v == index_symbol), index_symbol)
    bundle = load_dp_cycle_gui_bundle(
        index_symbol=index_symbol,
        high_low_start=high_low_start,
        refresh=refresh,
        skip_high_low=skip_high_low,
        mode=mode,
    )
    payload = bundle_to_web_payload(bundle, index_label=label, heavy=heavy)
    meta = bundle.get("meta") or {}
    # 入库后库 mtime 已变；读库结果写缓存，下次打开秒开
    save_web_payload_cache(str(meta.get("index_symbol") or index_symbol), payload, meta)
    return {"payload": payload, "meta": meta}


def bundle_to_web_payload(
    bundle: dict[str, Any], *, index_label: str = "", heavy: bool = True
) -> dict[str, Any]:
    """网页载荷：五项一律周频，与顶图周线同一时间网格，避免日频/稀疏点对不齐。"""
    weekly = bundle.get("weekly")
    anchor = _weekly_anchor_dates(weekly)
    # ── 性能优化：向量化替代 iterrows ──
    w_rows: list[dict[str, Any]] = []
    if weekly is not None and not weekly.empty:
        _dates = pd.to_datetime(weekly["date"], errors="coerce")
        _mask = _dates.notna()
        _d = _dates[_mask].dt.strftime("%Y-%m-%d")
        _o = weekly.loc[_mask, "open"].to_numpy(dtype=float)
        _h = weekly.loc[_mask, "high"].to_numpy(dtype=float)
        _l = weekly.loc[_mask, "low"].to_numpy(dtype=float)
        _c = weekly.loc[_mask, "close"].to_numpy(dtype=float)
        w_rows = [
            {"time": _d.iloc[i], "open": _o[i], "high": _h[i], "low": _l[i], "close": _c[i]}
            for i in range(len(_d))
        ]

    def _series_df(df: pd.DataFrame | None, value_col: str) -> list[dict[str, Any]]:
        if df is None or df.empty or value_col not in df.columns:
            return []
        _d = pd.to_datetime(df["date"], errors="coerce")
        _v = pd.to_numeric(df[value_col], errors="coerce")
        _mask = _d.notna() & _v.notna()
        _ds = _d[_mask].dt.strftime("%Y-%m-%d")
        _vs = _v[_mask].to_numpy(dtype=float)
        return [{"time": _ds.iloc[i], "value": _vs[i]} for i in range(len(_ds))]

    # 01 主力：对照 PDF —— 月净额主档 + 周净额副档（东财大盘主力净流入）
    mf_daily = bundle.get("main_force_daily")
    mf_w = _resample_to_weekly(mf_daily, "净额_亿元", how="sum", anchor=anchor)
    mf_m = pd.DataFrame(columns=["date", "净额_亿元"])
    if mf_daily is not None and not getattr(mf_daily, "empty", True) and "净额_亿元" in mf_daily.columns:
        x = mf_daily[["date", "净额_亿元"]].copy()
        x["date"] = pd.to_datetime(x["date"], errors="coerce")
        x["净额_亿元"] = pd.to_numeric(x["净额_亿元"], errors="coerce")
        x = x.dropna(subset=["date", "净额_亿元"]).set_index("date").sort_index()
        mf_m = x[["净额_亿元"]].resample("ME").sum(min_count=1).dropna().reset_index()

    # 北向辅源：周净买（长历史，对齐周 K）
    nb_w = _resample_to_weekly(bundle.get("northbound_daily"), "净额_亿元", how="sum", anchor=anchor)

    # 02 ETF：周末份额（存量）
    etf_w = _resample_to_weekly(bundle.get("etf_daily"), "份额_亿份", how="last", anchor=anchor)

    # 03 两融：周末融资余额
    margin_w = _resample_to_weekly(bundle.get("margin"), "融资余额_亿元", how="last", anchor=anchor)

    # 04 偏离：周末值
    gv_w = _resample_to_weekly(bundle.get("growth_value"), "偏离度", how="last", anchor=anchor)

    # 05 广度：周末家数
    hl = bundle.get("high_low")
    hl_rows: list[dict[str, Any]] = []
    if hl is not None and not hl.empty:
        parts = {}
        for col, how in (("new_high", "last"), ("new_low", "last"), ("新高-新低", "last")):
            if col not in hl.columns:
                continue
            w = _resample_to_weekly(hl, col, how=how, anchor=anchor)
            parts[col] = w.set_index("date")[col] if not w.empty else pd.Series(dtype=float)
        if parts:
            merged = pd.concat(parts, axis=1).dropna(how="all").reset_index()
            if "date" not in merged.columns:
                merged = merged.rename(columns={merged.columns[0]: "date"})
            # ── 向量化替代 iterrows ──
            _d = pd.to_datetime(merged["date"], errors="coerce")
            _mask = _d.notna()
            _ds = _d[_mask].dt.strftime("%Y-%m-%d")
            _nh = merged.loc[_mask, "new_high"].to_numpy(dtype=float) if "new_high" in merged.columns else None
            _nl = merged.loc[_mask, "new_low"].to_numpy(dtype=float) if "new_low" in merged.columns else None
            _df = merged.loc[_mask, "新高-新低"].to_numpy(dtype=float) if "新高-新低" in merged.columns else None
            _n = len(_ds)
            for i in range(_n):
                row: dict[str, Any] = {"time": _ds.iloc[i]}
                if _nh is not None and not pd.isna(_nh[i]):
                    row["new_high"] = _nh[i]
                if _nl is not None and not pd.isna(_nl[i]):
                    row["new_low"] = _nl[i]
                if _df is not None and not pd.isna(_df[i]):
                    row["diff"] = _df[i]
                hl_rows.append(row)

    meta = bundle.get("meta") or {}
    sym = meta.get("index_symbol") or ""
    quality = meta.get("quality") or {}
    qsum = quality.get("summary") or ""
    hl_title, hl_note, hl_sub = _high_low_captions(hl)
    unlock_daily = bundle.get("unlock_daily")
    from data_source.dp_cycle.unlock import aggregate_unlock_periods, extend_weekly_anchors

    anchor_ext = extend_weekly_anchors(anchor)
    unlock_m = aggregate_unlock_periods(unlock_daily, anchor, freq="ME")
    unlock_q = aggregate_unlock_periods(unlock_daily, anchor, freq="QE")
    real_last = pd.Timestamp(anchor.max()).normalize() if anchor is not None and len(anchor) else None
    axis_tail = []
    if real_last is not None:
        axis_tail = [
            pd.Timestamp(day).strftime("%Y-%m-%d")
            for day in anchor_ext
            if pd.Timestamp(day).normalize() > real_last
        ]

    def _unlock_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
        if frame is None or frame.empty:
            return []
        # ── 向量化替代 iterrows ──
        _d = pd.to_datetime(frame["date"], errors="coerce")
        _mask = _d.notna()
        _ds = _d[_mask].dt.strftime("%Y-%m-%d")
        _all = frame.loc[_mask, "全市场_亿元"].to_numpy(dtype=float) if "全市场_亿元" in frame.columns else None
        _star = frame.loc[_mask, "科创板_亿元"].to_numpy(dtype=float) if "科创板_亿元" in frame.columns else None
        _n = len(_ds)
        rows: list[dict[str, Any]] = []
        for i in range(_n):
            row: dict[str, Any] = {"time": _ds.iloc[i]}
            if _all is not None and not pd.isna(_all[i]):
                row["all"] = _all[i]
            if _star is not None and not pd.isna(_star[i]):
                row["star"] = _star[i]
            if "all" in row or "star" in row:
                rows.append(row)
        return rows

    unlock_m_rows = _unlock_rows(unlock_m)
    unlock_q_rows = _unlock_rows(unlock_q)
    subtitle = f"2015至今 · {index_label or sym} 周线 · 解禁含未来半年"
    if qsum:
        subtitle = f"{subtitle} · {qsum}"

    mf_m_rows = _series_df(mf_m, "净额_亿元")
    mf_w_rows = _series_df(mf_w, "净额_亿元")
    nb_rows = _series_df(nb_w, "净额_亿元")
    etf_rows = _series_df(etf_w, "份额_亿份")
    margin_rows = _series_df(margin_w, "融资余额_亿元")
    gv_rows = _series_df(gv_w, "偏离度")

    def _span(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        times = [str(r.get("time"))[:10] for r in rows if r.get("time")]
        if not times:
            return None
        return {"first": times[0], "last": times[-1], "n": len(times)}

    def _coverage() -> dict[str, Any]:
        return {
            "weekly": len(w_rows),
            "mf_month": len(mf_m_rows),
            "mf_week": len(mf_w_rows),
            "northbound_week": len(nb_rows),
            "etf": len(etf_rows),
            "margin": len(margin_rows),
            "growth_value": len(gv_rows),
            "high_low": len(hl_rows),
            "unlock_month": len(unlock_m_rows),
            "unlock_quarter": len(unlock_q_rows),
        }

    # 与周K同轴时，短窗/断档会显示为大片空白——这是源覆盖，不是联动故障
    coverage_span = {
        "weekly": _span(w_rows),
        "mf_month": _span(mf_m_rows),
        "mf_week": _span(mf_w_rows),
        "northbound_week": _span(nb_rows),
        "etf": _span(etf_rows),
        "margin": _span(margin_rows),
        "growth_value": _span(gv_rows),
        "high_low": _span(hl_rows),
        "unlock_month": _span(unlock_m_rows),
        "unlock_quarter": _span(unlock_q_rows),
    }
    coverage_note = {
        "mf_month": "东财大盘主力日K约近半年，月柱只落在窗内",
        "mf_week": "东财大盘主力日K约近半年，更早无公开长历史",
        "northbound_week": "东财「当日成交净买额」2024-08 后停更，右侧空白属源断档",
        "etf": "上交所核心宽基月末回填，历史短于周K属正常",
        "margin": None,
        "growth_value": None,
        "high_low": hl_note,
        "unlock_month": "实际解禁市值，含已排期的未来半年。柱是全市场，线是科创板",
        "unlock_quarter": "季合计，同样含未来半年里能落上的季度",
    }

    from data_source.dp_cycle.diagnosis import diagnose_position

    diagnosis = diagnose_position(bundle, heavy=heavy)

    return {
        "subtitle": subtitle,
        "grain": "week+month",
        "weekly": w_rows,
        "main_force_monthly": mf_m_rows,
        "main_force_weekly": mf_w_rows,
        "main_force": mf_w_rows,
        "northbound_weekly": nb_rows,
        "author_month_line": -4000.0,
        "author_week_line": -2000.0,
        "etf": etf_rows,
        "margin": margin_rows,
        "growth_value": gv_rows,
        "high_low": hl_rows,
        "high_low_label": hl_title,
        "unlock_monthly": unlock_m_rows,
        "unlock_quarterly": unlock_q_rows,
        "axis_tail": axis_tail,
        "diagnosis": diagnosis,
        "score_curve": diagnosis.get("score_curve") or [],
        "meta": meta,
        "quality": quality,
        "coverage": _coverage(),
        "coverage_span": coverage_span,
        "coverage_note": coverage_note,
        "liquidity": bundle.get("liquidity"),
    }


def write_web_snapshot(
    payload: dict[str, Any] | None = None,
    *,
    index_symbol: str = DEFAULT_WEEKLY_INDEX,
) -> str:
    """把当前库（或已有 payload）写成可离线打开的 web_snapshot。"""
    import json
    from pathlib import Path

    from output_paths import dp_cycle_store_dir

    if payload is None:
        label = next((k for k, v in INDEX_CHOICES.items() if v == index_symbol), index_symbol)
        bundle = load_dp_cycle_gui_bundle(index_symbol=index_symbol, refresh=False)
        payload = bundle_to_web_payload(bundle, index_label=label)

    web = Path(__file__).resolve().parents[3] / "web" / "dp_cycle"
    index = web / "index.html"
    out_dir = Path(dp_cycle_store_dir()) / "web_snapshot"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("lightweight-charts.js", "styles.css", "app.js"):
        src = web / name
        if src.is_file():
            (out_dir / name).write_bytes(src.read_bytes())
    html = index.read_text(encoding="utf-8")
    inject = (
        "<script>window.__DP_CYCLE_PAYLOAD__ = "
        + json.dumps(payload or {}, ensure_ascii=False)
        + ";</script>\n  <script src=\"app.js\"></script>"
    )
    html = html.replace('<script src="app.js"></script>', inject)
    snap = out_dir / "index.html"
    snap.write_text(html, encoding="utf-8")
    return str(snap.resolve())
