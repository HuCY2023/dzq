# -*- coding: utf-8 -*-
"""大周期五项指标 · 交叉验证（不绑死单一终端）。

原则：
- 主存序列不变；验证结果进 meta / 网页 quality 条
- 读库时做「表内一致性」快检；更新时可附带多源对账（两融已在 margin.py）
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def _ok(item: dict[str, Any], *, stable: bool, note: str, **extra: Any) -> dict[str, Any]:
    out = {"stable": stable, "note": note, **extra}
    out.update(item)
    return out


def _empty(name: str) -> dict[str, Any]:
    return {"id": name, "stable": None, "note": "无数据", "rows": 0}


def check_weekly_ohlc(weekly: pd.DataFrame) -> dict[str, Any]:
    """周 K：high/low 与 open/close 自洽。"""
    if weekly is None or getattr(weekly, "empty", True):
        return _empty("weekly")
    x = weekly.copy()
    need = {"open", "high", "low", "close"}
    if not need.issubset(set(x.columns)):
        return {"id": "weekly", "stable": False, "note": f"缺列 {need - set(x.columns)}", "rows": len(x)}
    for c in need:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.dropna(subset=list(need))
    bad = (
        (x["high"] + 1e-9 < x[["open", "close"]].max(axis=1))
        | (x["low"] - 1e-9 > x[["open", "close"]].min(axis=1))
        | (x["high"] < x["low"])
    )
    n_bad = int(bad.sum())
    return _ok(
        {"id": "weekly", "rows": int(len(x))},
        stable=n_bad == 0,
        note="OHLC 自洽" if n_bad == 0 else f"OHLC 异常 {n_bad} 根",
        bad=n_bad,
        last_close=float(x["close"].iloc[-1]) if len(x) else None,
        last_date=str(pd.to_datetime(x["date"].iloc[-1]).date()) if len(x) and "date" in x.columns else None,
    )


def check_main_force(mf: pd.DataFrame) -> dict[str, Any]:
    """01：有流入/流出列时核对净额；否则仅检查有限样本。"""
    if mf is None or getattr(mf, "empty", True):
        return _empty("main_force")
    x = mf.copy()
    x["净额_亿元"] = pd.to_numeric(x.get("净额_亿元"), errors="coerce")
    latest_date = pd.to_datetime(x["date"], errors="coerce").max() if "date" in x.columns else pd.NaT
    x = x.dropna(subset=["净额_亿元"])
    if x.empty:
        return {"id": "main_force", "stable": None, "note": "缺少主力净额", "rows": 0}
    note = f"{len(x)} 日样本（东财窗口偏短属预期）"
    stable = len(x) >= 20
    extra: dict[str, Any] = {"rows": int(len(x))}
    if {"流入_亿元", "流出_亿元"}.issubset(x.columns):
        inflow = pd.to_numeric(x["流入_亿元"], errors="coerce")
        outflow = pd.to_numeric(x["流出_亿元"], errors="coerce")
        # 部分源只有净额；有两边才验
        if inflow.notna().sum() > 10 and outflow.notna().sum() > 10:
            recon = (inflow - outflow - x["净额_亿元"]).abs()
            extra["recon_max"] = float(recon.max())
            extra["recon_mean"] = float(recon.mean())
            if recon.max() > 0.05:
                stable = False
                note = f"流入−流出≠净额 maxΔ={recon.max():.3f}"
            else:
                note = "流入−流出≈净额"
    extra["last"] = float(x["净额_亿元"].iloc[-1])
    if "date" in x.columns:
        last_valid = pd.to_datetime(x["date"], errors="coerce").max()
        extra["last_date"] = str(last_valid.date())
        if pd.notna(latest_date) and latest_date > last_valid:
            stable = False
            note += f"；净额有效至 {last_valid.date()}，{latest_date.date()} 净额缺失"
    return _ok({"id": "main_force"}, stable=stable, note=note, **extra)


def check_northbound(nb: pd.DataFrame) -> dict[str, Any]:
    """北向：表内有限性 + 覆盖提示（2024-08 后字段常空）。"""
    if nb is None or getattr(nb, "empty", True):
        return _empty("northbound")
    x = nb.copy()
    x["净额_亿元"] = pd.to_numeric(x.get("净额_亿元"), errors="coerce")
    x = x.dropna(subset=["净额_亿元"])
    last = pd.to_datetime(x["date"].iloc[-1]) if "date" in x.columns and len(x) else None
    stale = bool(last is not None and last < pd.Timestamp("2024-09-01"))
    note = f"{len(x)} 日有效净买"
    if stale:
        note += f" · 历史净买有效至 {last.date()}；2024-08-19 起披露机制变化"
    return _ok(
        {"id": "northbound", "rows": int(len(x))},
        stable=len(x) >= 100,
        note=note,
        last=float(x["净额_亿元"].iloc[-1]) if len(x) else None,
        last_date=str(last.date()) if last is not None else None,
        stale_after_2024q3=stale,
    )


def check_northbound_live_split() -> dict[str, Any]:
    """活检：北向 ≡ 沪股通 + 深股通（东财 HSGT）。失败不阻断。"""
    try:
        import akshare as ak

        from data_source.dp_cycle._http import call_with_retry

        def _prep(symbol: str, col: str) -> pd.DataFrame:
            raw = call_with_retry(ak.stock_hsgt_hist_em, symbol=symbol, retries=3, delay=1.2)
            d = raw.copy()
            d["date"] = pd.to_datetime(d["日期"], errors="coerce").dt.normalize()
            d[col] = pd.to_numeric(d.get("当日成交净买额"), errors="coerce")
            return d[["date", col]].dropna()

        nb = _prep("北向资金", "nb")
        h = _prep("沪股通", "h")
        s = _prep("深股通", "s")
        m = nb.merge(h, on="date").merge(s, on="date")
        m["diff"] = (m["nb"] - (m["h"] + m["s"])).abs()
        recent = m.tail(60)
        mx = float(recent["diff"].max()) if len(recent) else None
        return _ok(
            {"id": "northbound_live", "overlap": int(len(m)), "recent_n": int(len(recent))},
            stable=bool(mx is not None and mx < 1e-6),
            note="北向≡沪+深" if mx is not None and mx < 1e-6 else f"拆分对账 maxΔ={mx}",
            recent_abs_diff_max=mx,
        )
    except Exception as exc:  # noqa: BLE001
        return {"id": "northbound_live", "stable": None, "note": f"活检跳过: {type(exc).__name__}"}


def check_etf(etf_daily: pd.DataFrame, etf_hist: pd.DataFrame | None = None) -> dict[str, Any]:
    if etf_daily is None or getattr(etf_daily, "empty", True):
        return _empty("etf")
    x = etf_daily.copy()
    col = "份额_亿份" if "份额_亿份" in x.columns else None
    if not col:
        return {"id": "etf", "stable": False, "note": "缺份额列", "rows": len(x)}
    x[col] = pd.to_numeric(x[col], errors="coerce")
    x = x.dropna(subset=[col])
    neg = int((x[col] <= 0).sum())
    codes = 0
    coverage_note = ""
    source_status = None
    if etf_hist is not None and not getattr(etf_hist, "empty", True) and "code" in etf_hist.columns:
        from data_source.dp_cycle.national_team_etf import CORE_CSI300

        codes = int(etf_hist["code"].nunique())
        if "date" in etf_hist.columns and col in etf_hist.columns:
            hist = etf_hist.copy()
            hist["date"] = pd.to_datetime(hist["date"], errors="coerce")
            hist[col] = pd.to_numeric(hist[col], errors="coerce")
            hist = hist.dropna(subset=["date", "code", col])
            if not hist.empty:
                latest = hist.loc[hist["date"] == hist["date"].max(), "code"].astype(str).str.zfill(6)
                current = set(latest) & set(CORE_CSI300)
                coverage_note = f" · 最近覆盖 {len(current)}/{len(CORE_CSI300)} 只"
                source_status = "partial" if len(current) < len(CORE_CSI300) else None
    return _ok(
        {"id": "etf", "rows": int(len(x)), "codes": codes},
        stable=neg == 0 and len(x) >= 5,
        note=f"{len(x)} 个周末点" + (f" · {codes} 只ETF" if codes else "") + coverage_note + "（份额代理≠汇金/证金）",
        source_status=source_status,
        last=float(x[col].iloc[-1]),
        last_date=str(pd.to_datetime(x["date"].iloc[-1]).date()) if "date" in x.columns else None,
    )


def check_margin(margin: pd.DataFrame, *, live: bool = False) -> dict[str, Any]:
    if margin is None or getattr(margin, "empty", True):
        return _empty("margin")
    x = margin.copy()
    x["融资余额_亿元"] = pd.to_numeric(x.get("融资余额_亿元"), errors="coerce")
    x = x.dropna(subset=["融资余额_亿元"])
    if x.empty:
        return _empty("margin")
    # 表内：融资 ≤ 两融（若有）
    note = f"{len(x)} 日"
    stable = len(x) >= 200
    if "两融余额_亿元" in x.columns:
        tot = pd.to_numeric(x["两融余额_亿元"], errors="coerce")
        bad = int((x["融资余额_亿元"] - tot > 1.0).sum())  # 允许舍入
        if bad:
            stable = False
            note = f"融资>两融 异常 {bad} 日"
        else:
            note += " · 融资≤两融"
    out = _ok(
        {"id": "margin", "rows": int(len(x))},
        stable=stable,
        note=note,
        last=float(x["融资余额_亿元"].iloc[-1]),
        last_date=str(pd.to_datetime(x["date"].iloc[-1]).date()) if "date" in x.columns else None,
    )
    if live:
        try:
            from data_source.dp_cycle.margin import fetch_margin_daily

            _, meta = fetch_margin_daily(cross_check=True)
            xc = meta.get("cross_check") or {}
            out["live_stable"] = xc.get("stable")
            out["live_notes"] = xc.get("notes")
            if xc.get("stable") is False:
                out["stable"] = False
                out["note"] = (out.get("note") or "") + " · 多源 WARN"
            elif xc.get("stable") is True:
                out["note"] = (out.get("note") or "") + " · 多源 OK（SSE/东财）"
        except Exception as exc:  # noqa: BLE001
            out["live_error"] = f"{type(exc).__name__}: {exc}"
    return out


def check_growth_value(gv: pd.DataFrame) -> dict[str, Any]:
    if gv is None or getattr(gv, "empty", True):
        return _empty("growth_value")
    x = gv.copy()
    for c in ("PE比", "PB比", "偏离度"):
        if c in x.columns:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.dropna(subset=["偏离度"])
    recon_ok = True
    note = f"{len(x)} 点"
    if {"PE比", "PB比"}.issubset(x.columns):
        recon = ((x["PE比"] + x["PB比"]) / 2.0 - x["偏离度"]).abs()
        if float(recon.max()) > 1e-6:
            recon_ok = False
            note = f"公式复算偏差 max={float(recon.max()):.3g}"
        else:
            note += " · (PE+PB)/2 复算一致"
    outliers = int(((x["偏离度"] <= 0) | (x["偏离度"] > 50)).sum())
    return _ok(
        {"id": "growth_value", "rows": int(len(x)), "outliers": outliers},
        stable=recon_ok and outliers == 0 and len(x) >= 30,
        note=note if outliers == 0 else note + f" · 离群 {outliers}",
        last=float(x["偏离度"].iloc[-1]),
        last_date=str(pd.to_datetime(x["date"].iloc[-1]).date()) if "date" in x.columns else None,
    )


def check_high_low(hl: pd.DataFrame) -> dict[str, Any]:
    if hl is None or getattr(hl, "empty", True):
        return _empty("high_low")
    x = hl.copy()
    for c in ("new_high", "new_low"):
        if c in x.columns:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    diff_col = "新高-新低" if "新高-新低" in x.columns else ("diff" if "diff" in x.columns else None)
    if diff_col:
        x[diff_col] = pd.to_numeric(x[diff_col], errors="coerce")
    x = x.dropna(subset=["new_high", "new_low"])
    src = ""
    if "source" in x.columns and x["source"].notna().any():
        src = str(x["source"].dropna().iloc[0])
    if src == "lake_52w":
        note = f"{len(x)} 日 · K线湖252日（未剔ST，≠乐咕120日）"
    elif src == "legu_120d":
        note = f"{len(x)} 日 · 乐咕120日（≠作者52周）"
    else:
        note = f"{len(x)} 日"
    stable = len(x) >= 60
    if diff_col and len(x):
        recon = (x["new_high"] - x["new_low"] - x[diff_col]).abs()
        if float(recon.max()) > 0.5:
            stable = False
            note = f"差值复算异常 max={float(recon.max()):.3g}"
        else:
            note += " · high−low=diff"
    # 可选：与 hs300 子集缓存相关（不强制）
    return _ok(
        {"id": "high_low", "rows": int(len(x))},
        stable=stable,
        note=note,
        last_diff=float((x["new_high"] - x["new_low"]).iloc[-1]) if len(x) else None,
        last_date=str(pd.to_datetime(x["date"].iloc[-1]).date()) if "date" in x.columns and len(x) else None,
    )


def _rel_close(a: float, b: float, *, tol: float = 0.03, abs_tol: float = 0.5) -> bool:
    if abs(a - b) <= abs_tol:
        return True
    base = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / base <= tol


def check_unlock(unlock: pd.DataFrame, *, live: bool = False) -> list[dict[str, Any]]:
    """06：科创板≤全市场；更新时再用明细报表对一天。"""
    if unlock is None or getattr(unlock, "empty", True):
        return [_empty("unlock")]
    x = unlock.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce")
    x["amount_yi"] = pd.to_numeric(x.get("amount_yi"), errors="coerce")
    x = x.dropna(subset=["date", "amount_yi"])
    all_df = x.loc[x["universe"].astype(str) == "all"]
    star_df = x.loc[x["universe"].astype(str) == "star"]
    note = f"全市场 {len(all_df)} 日 · 科创板 {len(star_df)} 日 · 实际解禁市值"
    stable = len(all_df) >= 200 and len(star_df) >= 50
    if not all_df.empty and not star_df.empty:
        merged = all_df[["date", "amount_yi"]].merge(
            star_df[["date", "amount_yi"]],
            on="date",
            how="inner",
            suffixes=("_all", "_star"),
        )
        if not merged.empty:
            bad = merged["amount_yi_star"] > merged["amount_yi_all"] + 0.05
            n_bad = int(bad.sum())
            if n_bad:
                stable = False
                note = f"科创板大于全市场 {n_bad} 日"
            else:
                note += " · 科创板≤全市场"
    items = [
        _ok(
            {"id": "unlock", "rows": int(len(all_df))},
            stable=stable,
            note=note,
            last_date=str(all_df["date"].max().date()) if not all_df.empty else None,
        )
    ]
    if not live or all_df.empty:
        return items
    past = all_df.loc[all_df["date"] <= pd.Timestamp.today().normalize() - pd.Timedelta(days=3)]
    if past.empty:
        return items
    day = str(past["date"].max().date())
    try:
        from data_source.dp_cycle.unlock import cross_check_detail_day

        live_meta = cross_check_detail_day(day)
    except Exception as exc:  # noqa: BLE001
        items.append(
            {
                "id": "unlock_live",
                "stable": None,
                "note": f"明细对账跳过: {type(exc).__name__}",
                "rows": 0,
            }
        )
        return items
    if not live_meta.get("ok"):
        items.append(
            {
                "id": "unlock_live",
                "stable": None,
                "note": f"明细对账跳过: {live_meta.get('error') or '失败'}",
                "rows": 0,
            }
        )
        return items
    all_ok = _rel_close(float(live_meta["summary_all_yi"]), float(live_meta["detail_all_yi"]))
    star_ok = _rel_close(float(live_meta["summary_star_yi"]), float(live_meta["detail_star_yi"]))
    items.append(
        _ok(
            {"id": "unlock_live", "rows": int(live_meta.get("detail_n") or 0)},
            stable=all_ok and star_ok,
            note=(
                f"{day} 明细≡日统计"
                if all_ok and star_ok
                else f"{day} 明细与日统计偏差 全市场 {live_meta['detail_all_yi']:.1f}/{live_meta['summary_all_yi']:.1f}"
            ),
            day=day,
        )
    )
    return items


def validate_tables(tables: dict[str, Any], *, live: bool = False) -> dict[str, Any]:
    """对 load_from_db 结果做交叉/一致性验证。"""
    items = [
        check_weekly_ohlc(tables.get("weekly")),  # type: ignore[arg-type]
        check_main_force(tables.get("main_force_daily")),  # type: ignore[arg-type]
        check_northbound(tables.get("northbound_daily")),  # type: ignore[arg-type]
        check_etf(tables.get("etf_daily"), tables.get("etf_hist")),  # type: ignore[arg-type]
        check_margin(tables.get("margin"), live=live),  # type: ignore[arg-type]
        check_growth_value(tables.get("growth_value")),  # type: ignore[arg-type]
        check_high_low(tables.get("high_low")),  # type: ignore[arg-type]
    ]
    items.extend(check_unlock(tables.get("unlock_daily"), live=live))  # type: ignore[arg-type]
    if live:
        items.append(check_northbound_live_split())

    labels = {
        "weekly": "周K",
        "main_force": "01主力",
        "northbound": "北向",
        "northbound_live": "北向拆分",
        "etf": "02ETF",
        "margin": "03两融",
        "growth_value": "04偏离",
        "high_low": "05广度",
        "unlock": "06解禁",
        "unlock_live": "06明细",
    }
    badges: list[dict[str, Any]] = []
    for it in items:
        sid = it.get("id") or "?"
        st = it.get("stable")
        badges.append(
            {
                "id": sid,
                "label": labels.get(sid, sid),
                "stable": st,
                "note": it.get("note") or "",
                "rows": it.get("rows"),
                "source_status": "historical" if sid == "northbound" and it.get("stale_after_2024q3") else it.get("source_status"),
            }
        )

    scored = [b for b in badges if b["stable"] is not None]
    ok_n = sum(1 for b in scored if b["stable"] is True)
    bad_n = sum(1 for b in scored if b["stable"] is False)
    overall = "ok" if bad_n == 0 and ok_n >= 4 else ("warn" if bad_n <= 1 else "fail")
    scope = "一致性与来源核对" if live else "表内一致性检查"
    summary = f"{scope} {ok_n}/{len(scored)} 通过"
    if bad_n:
        summary += f" · {bad_n} 项告警"
    return {
        "overall": overall,
        "summary": summary,
        "badges": badges,
        "items": items,
        "live": bool(live),
    }
