# -*- coding: utf-8 -*-
"""大周期入库：按各序列自然颗粒度拉取并 upsert（2015+）。

建议刷新节奏（可按需点「更新入库」，不必死板月/季）：
- index_weekly：周
- margin / growth_value / high_low：有日频源则日更或周更
- main_force：THS 截面，适合每个交易日或每周落一点
- etf_shares：交易所日频点，周更足够跟踪申赎节奏
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from data_source.dp_cycle.store import (
    HISTORY_START_STR,
    clip_from_2015,
    connect,
    init_schema,
    log_run,
    touch_series,
    upsert_dataframe,
    _invalidate_load_cache,
)


def _last(df: pd.DataFrame, col: str = "date"):
    if df is None or df.empty:
        return None
    return pd.to_datetime(df[col]).max()


def ingest_index_weekly(con, symbol: str = "sh000001") -> dict[str, Any]:
    from data_source.dp_cycle.gui_bundle import fetch_weekly_index

    weekly, meta = fetch_weekly_index(symbol)
    weekly = clip_from_2015(weekly)
    if weekly.empty:
        return {**meta, "wrote": 0}
    out = weekly.copy()
    out["symbol"] = symbol
    out = out[["symbol", "date", "open", "high", "low", "close", "volume"]]
    out["date"] = pd.to_datetime(out["date"]).dt.date
    n = upsert_dataframe(con, "index_weekly", out, key_cols=["symbol", "date"])
    touch_series(con, "index_weekly", last_date=_last(weekly), note=f"symbol={symbol}")
    return {**meta, "wrote": n}


def ingest_main_force(con, *, as_of: date | None = None) -> dict[str, Any]:
    """优先东财大盘主力净流入日频（可聚月/周）；失败则用本地缓存；THS 截面仅作当日备源。"""
    import os

    from output_paths import dp_cycle_store_dir
    from data_source.dp_cycle.main_force_em import fetch_em_market_fund_flow, load_em_fund_flow_cache

    del as_of
    daily, meta = fetch_em_market_fund_flow()
    cache_json = os.path.join(dp_cycle_store_dir(), "_em_fflow_raw.json")
    cache_csv = os.path.join(dp_cycle_store_dir(), "main_force_ths", "em_market_daily.csv")
    cached = load_em_fund_flow_cache(cache_json)
    if cached.empty:
        cached = load_em_fund_flow_cache(cache_csv)
    try:
        stored = con.execute(
            """
            SELECT date, net_yi AS 净额_亿元, inflow_yi AS 流入_亿元,
                   outflow_yi AS 流出_亿元, industry_n AS 行业数, source
            FROM main_force_daily
            """
        ).df()
    except Exception:
        stored = pd.DataFrame()
    from data_source.dp_cycle.main_force_em import union_main_force_frames

    # 缓存、库内、本次拉取按日期并集；短响应不能把更长历史盖掉
    daily = union_main_force_frames(cached, stored, daily)
    if not daily.empty and not meta.get("ok"):
        meta = {
            **meta,
            "ok": True,
            "from_cache": True,
            "rows": int(len(daily)),
        }
    elif not daily.empty:
        meta["rows_merged"] = int(len(daily))

    if daily.empty:
        # 最后才用 THS 截面（与东财口径不同，仅避免完全空白）
        from data_source.dp_cycle.main_force_ths import upsert_today_from_ths

        daily, ths_meta = upsert_today_from_ths()
        meta = {**meta, "ths_fallback": ths_meta}
        source = "ths_industry_sum"
    else:
        source = "em_market_main"

    daily = clip_from_2015(daily)
    if daily.empty:
        return {**meta, "wrote": 0}

    # 落盘 CSV 便于离线
    try:
        store = os.path.join(dp_cycle_store_dir(), "main_force_ths")
        os.makedirs(store, exist_ok=True)
        path = os.path.join(store, "em_market_daily.csv")
        keep_cols = [c for c in ("date", "净额_亿元", "流入_亿元", "流出_亿元", "行业数") if c in daily.columns]
        daily[keep_cols].to_csv(path, index=False, encoding="utf-8-sig")
    except Exception:
        pass

    def _num(col: str):
        if col not in daily.columns:
            return pd.Series([pd.NA] * len(daily))
        return pd.to_numeric(daily[col], errors="coerce")

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(daily["date"]).dt.date,
            "net_yi": _num("净额_亿元"),
            "inflow_yi": _num("流入_亿元"),
            "outflow_yi": _num("流出_亿元"),
            "industry_n": pd.to_numeric(daily.get("行业数"), errors="coerce").astype("Int64")
            if "行业数" in daily.columns
            else pd.NA,
            "source": source,
            "small_yi": _num("小单_亿元"),
            "mid_yi": _num("中单_亿元"),
            "large_yi": _num("大单_亿元"),
            "super_yi": _num("超大单_亿元"),
        }
    )
    n = upsert_dataframe(con, "main_force_daily", out, key_cols=["date"])
    touch_series(con, "main_force_daily", last_date=_last(daily), note=source)
    meta["wrote"] = n
    meta["source"] = source
    return meta

def ingest_etf_shares(con, *, as_of: date | None = None, backfill_months: int = 60) -> dict[str, Any]:
    from data_source.dp_cycle.national_team_etf import backfill_core_etf_month_ends, snapshot_core_etfs

    hist, meta = snapshot_core_etfs(as_of=as_of)
    # 按月末回填缺口（已有月份跳过；默认约 5 年）
    if backfill_months > 0:
        hist2, bf_meta = backfill_core_etf_month_ends(months=backfill_months)
        meta["backfill"] = bf_meta
        if not hist2.empty:
            hist = hist2
    hist = clip_from_2015(hist)
    if hist.empty:
        return {**meta, "wrote": 0}
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(hist["date"]).dt.date,
            "code": hist["code"].astype(str),
            "name": hist.get("name", ""),
            "shares": pd.to_numeric(hist.get("基金份额"), errors="coerce"),
            "shares_yi": pd.to_numeric(hist.get("份额_亿份"), errors="coerce"),
        }
    )
    n = upsert_dataframe(con, "etf_shares_daily", out, key_cols=["date", "code"])
    touch_series(con, "etf_shares_daily", last_date=_last(hist), note="SSE核心宽基+月末回填")
    return {**meta, "wrote": n}

def ingest_margin(con, *, lookback_years: int = 5) -> dict[str, Any]:
    from data_source.dp_cycle.margin import enrich_margin, fetch_margin_daily

    raw, meta = fetch_margin_daily(cross_check=True)
    enriched = enrich_margin(raw, lookback_years=lookback_years)
    enriched = clip_from_2015(enriched)
    if enriched.empty:
        return {**meta, "wrote": 0}
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(enriched["date"]).dt.date,
            "financing_yi": pd.to_numeric(enriched["融资余额_亿元"], errors="coerce"),
            "margin_total_yi": pd.to_numeric(enriched.get("两融余额_亿元"), errors="coerce"),
            "pct_all": pd.to_numeric(enriched.get("分位_全样本"), errors="coerce"),
            "pct_5y": pd.to_numeric(enriched.get("分位_近N年"), errors="coerce"),
        }
    )
    n = upsert_dataframe(con, "margin_daily", out, key_cols=["date"])
    xc = meta.get("cross_check") or {}
    note = "沪深融资余额"
    if xc.get("stable") is True:
        note += " · xcheck OK"
    elif xc.get("stable") is False:
        note += " · xcheck WARN"
    touch_series(con, "margin_daily", last_date=_last(enriched), note=note)
    return {**meta, "wrote": n}


def ingest_growth_value(con) -> dict[str, Any]:
    from data_source.dp_cycle.growth_value import fetch_growth_value_divergence

    gv, meta = fetch_growth_value_divergence()
    gv = clip_from_2015(gv)
    if gv.empty:
        return {**meta, "wrote": 0}
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(gv["date"]).dt.date,
            "pe_ratio": pd.to_numeric(gv.get("PE比"), errors="coerce"),
            "pb_ratio": pd.to_numeric(gv.get("PB比"), errors="coerce"),
            "divergence": pd.to_numeric(gv.get("偏离度"), errors="coerce"),
            "g_pe": pd.to_numeric(gv.get("g_pe"), errors="coerce"),
            "v_pe": pd.to_numeric(gv.get("v_pe"), errors="coerce"),
            "g_pb": pd.to_numeric(gv.get("g_pb"), errors="coerce"),
            "v_pb": pd.to_numeric(gv.get("v_pb"), errors="coerce"),
        }
    )
    n = upsert_dataframe(con, "growth_value_daily", out, key_cols=["date"])
    touch_series(con, "growth_value_daily", last_date=_last(gv), note="创业板50/上证50")
    return {**meta, "wrote": n}


def ingest_northbound(con) -> dict[str, Any]:
    from data_source.dp_cycle.northbound import fetch_northbound_daily

    daily, meta = fetch_northbound_daily()
    daily = clip_from_2015(daily)
    if daily.empty:
        return {**meta, "wrote": 0}
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(daily["date"]).dt.date,
            "net_yi": pd.to_numeric(daily["净额_亿元"], errors="coerce"),
            "source": "em_hsgt_north",
        }
    )
    n = upsert_dataframe(con, "northbound_daily", out, key_cols=["date"])
    touch_series(con, "northbound_daily", last_date=_last(daily), note="北向净买辅源")
    return {**meta, "wrote": n}


def ingest_high_low_legu(con) -> dict[str, Any]:
    """乐咕 120 日新高新低快径（≠52周）。"""
    from data_source.dp_cycle.high_low_legu import fetch_legu_high_low

    hl, meta = fetch_legu_high_low("all")
    hl = clip_from_2015(hl)
    if hl.empty:
        return {**meta, "wrote": 0}
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(hl["date"]).dt.date,
            "new_high": pd.to_numeric(hl.get("new_high"), errors="coerce").astype("Int64"),
            "new_low": pd.to_numeric(hl.get("new_low"), errors="coerce").astype("Int64"),
            "diff": pd.to_numeric(hl.get("diff"), errors="coerce").astype("Int64"),
            "universe": pd.NA,
            "index_close": pd.to_numeric(hl.get("index_close"), errors="coerce"),
            "source": "legu_120d",
        }
    )
    n = upsert_dataframe(con, "high_low_daily", out, key_cols=["date", "source"])
    touch_series(con, "high_low_daily", last_date=_last(hl), note="legu_120d另存；有湖扫时网页不拼接")
    return {**meta, "wrote": n}


def ingest_high_low_lake(con, *, start: str = HISTORY_START_STR) -> dict[str, Any]:
    from data_source.dp_cycle.high_low_lake import (
        fetch_52w_high_low,
        fetch_index_close,
        merge_high_low_with_index,
    )

    hl, meta = fetch_52w_high_low(start=start)
    idx, idx_meta = fetch_index_close("sh000680")
    hl_m = merge_high_low_with_index(hl, idx)
    hl_m = clip_from_2015(hl_m)
    meta["index"] = idx_meta
    if hl_m.empty:
        return {**meta, "wrote": 0}
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(hl_m["date"]).dt.date,
            "new_high": pd.to_numeric(hl_m.get("new_high"), errors="coerce").astype("Int64"),
            "new_low": pd.to_numeric(hl_m.get("new_low"), errors="coerce").astype("Int64"),
            "diff": pd.to_numeric(hl_m.get("新高-新低"), errors="coerce").astype("Int64"),
            "universe": pd.to_numeric(hl_m.get("universe"), errors="coerce").astype("Int64"),
            "index_close": pd.to_numeric(hl_m.get("index_close"), errors="coerce"),
            "source": "lake_52w",
        }
    )
    n = upsert_dataframe(con, "high_low_daily", out, key_cols=["date", "source"])
    touch_series(
        con,
        "high_low_daily",
        last_date=_last(hl_m),
        note=f"展示用 lake_52w（未剔ST） from {start}；乐咕120日另存不拼接",
    )
    return {**meta, "wrote": n}


def ingest_unlock(con) -> dict[str, Any]:
    """06 全市场 / 科创板解禁日统计（实际+计划市值）。"""
    from data_source.dp_cycle.unlock import fetch_unlock_daily

    daily, meta = fetch_unlock_daily()
    daily = clip_from_2015(daily)
    if daily.empty:
        return {**meta, "wrote": 0}
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(daily["date"]).dt.date,
            "universe": daily["universe"].astype(str),
            "amount_yi": pd.to_numeric(daily["amount_yi"], errors="coerce"),
            "plan_yi": pd.to_numeric(daily.get("plan_yi"), errors="coerce"),
            "stock_n": pd.to_numeric(daily.get("stock_n"), errors="coerce").astype("Int64"),
            "source": daily.get("source", "em_liftday_sta").astype(str),
        }
    )
    n = upsert_dataframe(con, "unlock_daily", out, key_cols=["date", "universe"])
    touch_series(con, "unlock_daily", last_date=_last(daily), note="东财实际解禁市值 全市场+科创板")
    return {**meta, "wrote": n}


def ingest_fund_issue(con) -> dict[str, Any]:
    from data_source.dp_cycle.fund_issue import fetch_fund_issues

    raw, meta = fetch_fund_issues()
    raw = clip_from_2015(raw, date_col="found_date") if not raw.empty else raw
    if raw is None or raw.empty:
        return {**meta, "wrote": 0}
    out = pd.DataFrame(
        {
            "code": raw["code"].astype(str),
            "name": raw["name"].astype(str),
            "found_date": pd.to_datetime(raw["found_date"]).dt.date,
            "shares_yi": pd.to_numeric(raw["shares_yi"], errors="coerce"),
            "fund_type": raw["fund_type"].astype(str),
        }
    )
    n = upsert_dataframe(con, "fund_issue", out, key_cols=["code"])
    touch_series(con, "fund_issue", last_date=_last(out, "found_date"), note="东财新成立募集份额")
    return {**meta, "wrote": n}


def ingest_holder_reduce(con) -> dict[str, Any]:
    from data_source.dp_cycle.holder_reduce import fetch_holder_reduces

    raw, meta = fetch_holder_reduces(start=HISTORY_START_STR)
    if raw is None or raw.empty:
        return {**meta, "wrote": 0}
    out = pd.DataFrame(
        {
            "code": raw["code"].astype(str),
            "holder": raw["holder"].astype(str),
            "end_date": pd.to_datetime(raw["end_date"]).dt.date,
            "notice_date": pd.to_datetime(raw["notice_date"]).dt.date,
            "change_wan": pd.to_numeric(raw["change_wan"], errors="coerce"),
            "close_price": pd.to_numeric(raw["close_price"], errors="coerce"),
            "amount_yi": pd.to_numeric(raw["amount_yi"], errors="coerce"),
        }
    )
    n = upsert_dataframe(con, "holder_reduce", out, key_cols=["code", "holder", "end_date", "notice_date"])
    touch_series(con, "holder_reduce", last_date=_last(out, "end_date"), note="东财重要股东减持")
    return {**meta, "wrote": n}


def ingest_industry_flow(con) -> dict[str, Any]:
    from data_source.dp_cycle.industry_flow import fetch_industry_flows

    raw, meta = fetch_industry_flows()
    raw = clip_from_2015(raw) if raw is not None and not raw.empty else raw
    if raw is None or raw.empty:
        return {**meta, "wrote": 0}
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["date"]).dt.date,
            "bk_code": raw["bk_code"].astype(str),
            "name": raw["name"].astype(str),
            "net_yi": pd.to_numeric(raw["net_yi"], errors="coerce"),
            "mid_yi": pd.to_numeric(raw["mid_yi"], errors="coerce"),
            "small_yi": pd.to_numeric(raw["small_yi"], errors="coerce"),
        }
    )
    n = upsert_dataframe(con, "industry_flow_daily", out, key_cols=["date", "bk_code"])
    touch_series(con, "industry_flow_daily", last_date=_last(out), note="东财行业主力近端日频")
    return {**meta, "wrote": n}


def update_dp_cycle_db(
    *,
    mode: str = "auto",
    index_symbol: str = "sh000001",
    high_low_start: str = HISTORY_START_STR,
    as_of: date | None = None,
    skip_high_low: bool = False,
) -> dict[str, Any]:
    """
    全量/增量入库（各表按源数据覆盖 2015+）。

    mode 仅作日志标签：auto / weekly / monthly / manual 等，不改变取数逻辑。
    skip_high_low：跳过 K 线湖 52 周扫库；仍会拉乐咕 120 日快径。
    """
    con = connect()
    _invalidate_load_cache()  # 入库前使缓存失效
    detail: dict[str, Any] = {"mode": mode, "index_symbol": index_symbol}
    ok = True
    try:
        init_schema(con)
        detail["index_weekly"] = ingest_index_weekly(con, index_symbol)
        detail["main_force"] = ingest_main_force(con, as_of=as_of)
        detail["northbound"] = ingest_northbound(con)
        detail["etf"] = ingest_etf_shares(con, as_of=as_of)
        detail["margin"] = ingest_margin(con)
        detail["growth_value"] = ingest_growth_value(con)
        detail["unlock"] = ingest_unlock(con)
        detail["fund_issue"] = ingest_fund_issue(con)
        detail["holder_reduce"] = ingest_holder_reduce(con)
        detail["industry_flow"] = ingest_industry_flow(con)
        detail["high_low_legu"] = ingest_high_low_legu(con)
        if skip_high_low:
            detail["high_low_lake"] = {"ok": True, "wrote": 0, "skipped": True}
        else:
            detail["high_low_lake"] = ingest_high_low_lake(con, start=high_low_start)

        for k, v in detail.items():
            if isinstance(v, dict) and v.get("ok") is False:
                ok = False
        log_run(con, mode=mode, ok=ok, detail=str({k: (v.get("wrote") if isinstance(v, dict) else v) for k, v in detail.items()}))
        detail["ok"] = ok
        return detail
    except Exception as exc:  # noqa: BLE001
        log_run(con, mode=mode, ok=False, detail=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        con.close()
