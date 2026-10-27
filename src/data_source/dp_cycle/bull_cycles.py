# -*- coding: utf-8 -*-
"""牛熊启动专栏：上证周 K + 点火 / 第二棒 / 结束节点。

周线优先读本地缓存（可覆盖 2008+）；缓存缺失时用 AkShare 拉日线再聚周。
大周期 DuckDB 的 index_weekly 仅 2015+，不够画完整牛熊史。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

# 与专栏「历次牛熊」对齐。金额为公开量级/专栏口径，不是交易所逐笔对账。
# 字段：buyer/buy_amt 买入侧；seller/sell_amt 卖出或退出侧；how 怎么操作；text 图上短标。
BULL_CYCLE_NODES: list[dict[str, str]] = [
    # —— 2009 牛 ——
    {
        "time": "2008-11-07",
        "kind": "ignite",
        "cycle": "2009牛",
        "text": "点火·汇金+四万亿",
        "buyer": "汇金（二级买工/中/建）→ 随后财政四万亿定调",
        "buy_amt": "汇金增持约数百亿级；四万亿是财政总盘子，不是当日买股额",
        "seller": "—（恐慌盘仍在出，但政策侧开始不计价买）",
        "sell_amt": "—",
        "how": "先国家队买银行股托信，再四万亿打开风险偏好",
        "note": "真正点火：汇金买三大行 + 四万亿",
    },
    {
        "time": "2009-03-06",
        "kind": "relay",
        "cycle": "2009牛",
        "text": "第二棒·信贷9.6万亿",
        "buyer": "银行信贷 → 实体/地产链/周期（钱进实体再回流股市）",
        "buy_amt": "2009年新增人民币信贷约9.6万亿",
        "seller": "—",
        "sell_amt": "—",
        "how": "财政点火后，信贷接棒持续几个季度",
        "note": "第二棒是信贷，不是又一轮国家队",
    },
    {
        "time": "2009-08-07",
        "kind": "end",
        "cycle": "2009牛",
        "text": "结束·收信贷",
        "buyer": "—",
        "buy_amt": "—",
        "seller": "政策侧回收流动性；市场高位抛压",
        "sell_amt": "信贷增速回落（相对峰值退潮），领先2010年加息见顶",
        "how": "担心通胀 → 收信贷 → 牛结束",
        "note": "死法：政策把信贷收回",
    },
    # —— 2014–15 配资牛 ——
    {
        "time": "2014-11-21",
        "kind": "ignite",
        "cycle": "2014–15配资牛",
        "text": "点火·降息加速",
        "buyer": "改革叙事下的风险偏好资金；降息后杠杆资金开始进场",
        "buy_amt": "降息是加速器（非单笔买入额）；上证自约2000附近起涨",
        "seller": "—",
        "sell_amt": "—",
        "how": "叙事点火 + 降息打开杠杆空间",
        "note": "民间配资牛的点火加速点",
    },
    {
        "time": "2015-03-13",
        "kind": "relay",
        "cycle": "2014–15配资牛",
        "text": "第二棒·两融→2.3万亿",
        "buyer": "两融客户 + 场外配资（散户杠杆）",
        "buy_amt": "两融余额约4千亿增至约2.3万亿；场外配资另有数倍杠杆（量级估计）",
        "seller": "—",
        "sell_amt": "—",
        "how": "杠杆资金接棒，从小票扩到全市场",
        "note": "第二棒是配资杠杆，不是国家队",
    },
    {
        "time": "2015-06-12",
        "kind": "end",
        "cycle": "2014–15配资牛",
        "text": "结束·清配资5178",
        "buyer": "—（随后才有国家队救火，那是托举不是点火）",
        "buy_amt": "—",
        "seller": "监管清理配资 → 杠杆被动平仓；高位主力与游资兑现",
        "sell_amt": "两融从约2.3万亿快速去化；场外配资被拔掉电源（强制卖）",
        "how": "监管拔掉配资电源 → 踩踏式卖出",
        "note": "死法：杀杠杆",
    },
    # —— 2017 结构牛（熊内）——
    {
        "time": "2017-01-13",
        "kind": "relay",
        "cycle": "2017漂亮50",
        "text": "第二棒·陆股通定价",
        "buyer": "陆股通/北向 + 机构抱核心资产",
        "buy_amt": "北向持续净流入（年千亿级量级波动）；主板指数未必大涨",
        "seller": "监管压壳与题材，游资/小票资金被挤出",
        "sell_amt": "题材股流动性收缩（结构切换，非全市场单笔）",
        "how": "外资慢钱定价，漂亮50结构牛",
        "note": "熊市里的结构第二棒",
    },
    {
        "time": "2019-01-04",
        "kind": "end",
        "cycle": "2015–19熊底",
        "text": "结束·杀到2440",
        "buyer": "—（随后才是新一轮点火）",
        "buy_amt": "—",
        "seller": "金融去杠杆尾声 + 贸易战压力下的抛售",
        "sell_amt": "上证自2018初约3587再杀至约2440（点位，非单笔额）",
        "how": "熊市末端流动性与风险偏好双杀",
        "note": "熊结束点 = 下一轮点火前夜",
    },
    # —— 2019–21 机构牛 ——
    {
        "time": "2019-01-04",
        "kind": "ignite",
        "cycle": "2019–21机构牛",
        "text": "点火·宽信用估值低",
        "buyer": "政策宽信用/降准打开空间；估值极低处开始有增量",
        "buy_amt": "降准释放长期资金（万亿级流动性口径，非当日买股）",
        "seller": "—",
        "sell_amt": "—",
        "how": "极低位置 + 宽信用 = 点火条件",
        "note": "机构牛点火偏「慢热」",
    },
    {
        "time": "2020-04-03",
        "kind": "relay",
        "cycle": "2019–21机构牛",
        "text": "第二棒·北向+公募",
        "buyer": "北向资金 + 公募发行（消费医药→新能源）",
        "buy_amt": "北向疫情后来回百亿级日净买常见；公募爆款发行千亿级批次",
        "seller": "—",
        "sell_amt": "—",
        "how": "外资先买核心资产，公募接棒扩散",
        "note": "第二棒是机构慢钱",
    },
    {
        "time": "2021-02-18",
        "kind": "end",
        "cycle": "2019–21机构牛",
        "text": "结束·抱团瓦解",
        "buyer": "—",
        "buy_amt": "—",
        "seller": "公募/抱团资金高位兑现；核心资产拥挤后杀估值",
        "sell_amt": "抱团股从高位大幅回撤（结构卖出，指数回撤小于个股）",
        "how": "高位主力/机构抽血 + 拥挤交易反转",
        "note": "死法：高位主力抽血",
    },
    # —— 2021–24 熊内托举（不是点火）——
    {
        "time": "2024-02-02",
        "kind": "relay",
        "cycle": "2021–24熊·托举",
        "text": "托举·汇金买ETF",
        "buyer": "汇金等买入宽基ETF（托底）",
        "buy_amt": "公开报道累计增持ETF约数百亿至千亿级量级（分批，非单日）",
        "seller": "融资/北向与弱势盘仍在出",
        "sell_amt": "托住下跌，但未扭转趋势（专栏：只托住下跌）",
        "how": "救火/托举，对齐2015夏国家队功能——不是下一轮点火",
        "note": "重要：这是托举，不是点火",
    },
    # —— 2024–26 政策配资牛 ——
    {
        "time": "2024-09-24",
        "kind": "ignite",
        "cycle": "2024–26政策配资牛",
        "text": "点火·央行配资924",
        "buyer": "央行当「配资商」：互换便利/股票回购再贷款；汇金证金买沪深300、上证50",
        "buy_amt": "政策工具额度：互换便利约5000亿 + 回购再贷款约3000亿（额度≠当日成交）；汇金证金买宽基分批",
        "seller": "—（空头回补与踏空盘短期也在买）",
        "sell_amt": "—",
        "how": "央行把信用接到股票抵押/回购贷款；自己不在二级市场挂单，汇金证金下场买宽基",
        "note": "船长原话：央行充当最大配资商",
    },
    {
        "time": "2024-10-08",
        "kind": "relay",
        "cycle": "2024–26政策配资牛",
        "text": "第二棒·融资脉冲",
        "buyer": "融资盘（三次脉冲）+ 风险偏好资金；宽基仍由国家队托",
        "buy_amt": "10/8全市场成交约3.5万亿；融资余额随后脉冲抬升（较924前显著增加，量级看大周期融资图）",
        "seller": "—",
        "sell_amt": "—",
        "how": "国家队托指数，融资推科创/弹性票",
        "note": "第二棒偏融资，盈利接棒弱",
    },
    {
        "time": "2026-07-03",
        "kind": "end",
        "cycle": "2024–26政策配资牛",
        "text": "结束·国家队降温+去杠杆",
        "buyer": "—",
        "buy_amt": "—",
        "seller": "国家队高位减/降温；融资盘去杠杆出清",
        "sell_amt": "2026上半年国家队转为降温；7月杠杆与拥挤一起出清（余额去化量级见融资序列）",
        "how": "高位国家队减 + 杀杠杆，双杀结束段",
        "note": "死法：国家队高位减，杠杆分位到顶后去化",
    },
]


def _cache_path() -> str:
    from output_paths import dp_cycle_store_dir

    root = dp_cycle_store_dir()
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "bulls_weekly_sh000001.json")


def _daily_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce")
    x = x.dropna(subset=["date"]).set_index("date").sort_index()
    for c in ("open", "high", "low", "close", "volume"):
        if c not in x.columns:
            x[c] = pd.NA
        else:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    w = x.resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return w.dropna(subset=["close"]).reset_index()


def _rows_from_weekly(weekly: pd.DataFrame) -> list[dict[str, Any]]:
    if weekly is None or weekly.empty:
        return []
    d = pd.to_datetime(weekly["date"], errors="coerce")
    mask = d.notna()
    ds = d[mask].dt.strftime("%Y-%m-%d")
    o = weekly.loc[mask, "open"].to_numpy(dtype=float)
    h = weekly.loc[mask, "high"].to_numpy(dtype=float)
    l = weekly.loc[mask, "low"].to_numpy(dtype=float)
    c = weekly.loc[mask, "close"].to_numpy(dtype=float)
    import math

    out: list[dict[str, Any]] = []
    for i in range(len(ds)):
        vals = (float(o[i]), float(h[i]), float(l[i]), float(c[i]))
        if not all(math.isfinite(v) for v in vals):
            continue
        out.append(
            {
                "time": ds.iloc[i],
                "open": vals[0],
                "high": vals[1],
                "low": vals[2],
                "close": vals[3],
            }
        )
    return out


def _load_cache(*, max_age_hours: float = 24 * 7) -> dict[str, Any] | None:
    path = _cache_path()
    if not os.path.isfile(path):
        return None
    try:
        raw = json.loads(open(path, encoding="utf-8").read())
    except Exception:
        return None
    weekly = raw.get("weekly") or []
    if len(weekly) < 100:
        return None
    saved = str(raw.get("saved_at") or "")
    try:
        ts = datetime.fromisoformat(saved.replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 3600.0
        if age_h > max_age_hours:
            return None
    except Exception:
        pass
    return raw


def _save_cache(payload: dict[str, Any]) -> None:
    path = _cache_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


def _fetch_weekly_ak() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from data_source.dp_cycle._http import call_with_retry

    meta: dict[str, Any] = {"source": "akshare.stock_zh_index_daily(sh000001)", "ok": False}
    try:
        import akshare as ak

        raw = call_with_retry(ak.stock_zh_index_daily, symbol="sh000001")
        daily = raw.copy()
        daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
        for c in ("open", "high", "low", "close", "volume"):
            if c in daily.columns:
                daily[c] = pd.to_numeric(daily[c], errors="coerce")
        weekly = _daily_to_weekly(daily[["date", "open", "high", "low", "close", "volume"]])
        # 牛熊专栏从 2007 年底画起
        weekly = weekly.loc[pd.to_datetime(weekly["date"]) >= pd.Timestamp("2007-01-01")]
        rows = _rows_from_weekly(weekly)
        meta.update(
            {
                "ok": True,
                "rows": len(rows),
                "first": rows[0]["time"] if rows else None,
                "last": rows[-1]["time"] if rows else None,
            }
        )
        return rows, meta
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return [], meta


def _weekly_from_db() -> list[dict[str, Any]]:
    try:
        from data_source.dp_cycle.store import connect

        con = connect(read_only=True)
        try:
            df = con.execute(
                """
                SELECT date, open, high, low, close
                FROM index_weekly
                WHERE symbol = 'sh000001'
                ORDER BY date
                """
            ).df()
        finally:
            con.close()
        if df.empty:
            return []
        df = df.rename(columns={"date": "date"})
        return _rows_from_weekly(df)
    except Exception:
        return []


def load_bulls_chart_payload(*, force_refresh: bool = False) -> dict[str, Any]:
    """供 GUI 注入：weekly + nodes + meta。"""
    cached = None if force_refresh else _load_cache()
    weekly: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    if cached:
        weekly = list(cached.get("weekly") or [])
        meta = dict(cached.get("meta") or {})
        meta["from_cache"] = True
    if len(weekly) < 100:
        weekly, meta = _fetch_weekly_ak()
        meta["from_cache"] = False
        if weekly:
            _save_cache(
                {
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "weekly": weekly,
                    "meta": meta,
                }
            )
    if len(weekly) < 50:
        # 兜底：库内 2015+ 周线
        db_rows = _weekly_from_db()
        if db_rows:
            weekly = db_rows
            meta = {
                "ok": True,
                "source": "duckdb.index_weekly",
                "rows": len(weekly),
                "first": weekly[0]["time"],
                "last": weekly[-1]["time"],
                "note": "仅库内2015+；完整2008史需联网缓存一次",
            }

    return {
        "symbol": "sh000001",
        "title": "上证指数 · 周K",
        "weekly": weekly,
        "nodes": list(BULL_CYCLE_NODES),
        "meta": meta,
        "legend": [
            {"kind": "ignite", "label": "点火", "color": "#f5c542"},
            {"kind": "relay", "label": "第二棒", "color": "#5ad8a6"},
            {"kind": "end", "label": "结束", "color": "#ef8b7a"},
        ],
    }
