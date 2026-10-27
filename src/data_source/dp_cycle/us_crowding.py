# -*- coding: utf-8 -*-
"""美股交易拥挤度跟踪 — 大周期框架扩展指标。

数据来源（全部免费、无需 API Key）：
- SOX 费城半导体指数：akshare macro_global_sox_index()
- S&P 500 / Nasdaq：akshare index_us_stock_sina()
- VIX 恐慌指数：FRED CSV (VIXCLS)
- VXV 3月VIX：FRED CSV (VXVCLS)
- M2 货币供应：FRED CSV (M2SL)
- GDP：FRED CSV (GDP)
- Shiller CAPE：Yale 大学 XLS
- HY/IG 信用利差：FRED CSV (BAMLH0A0HYM2, BAMLC0A4CBBB)
- 美联储资产负债表：FRED CSV (WALCL, TREAST)
- 隔夜逆回购：FRED CSV (RRPONTSYD)
- 国债收益率：FRED CSV (DGS10, DGS2, DTB3)
- FINRA 保证金债务：FINRA 官网月度统计表

核心指标（大周期框架 §3.D 扩展位）：

一、杠杆/债务类
1. 保证金债务 / M2 ≥ 6% → 历史级预警
2. 保证金债务 / GDP ≥ 4.5% → 经济占比过高

二、估值类
3. Shiller CAPE ≥ 35 → 历史极高位
4. 巴菲特指标（总市值/GDP）≥ 200% → 严重高估

三、波动率/衍生品结构
5. VIX 历史分位 ≥ 60% → 市场脆弱
6. VIX/VXV 期限结构倒挂（>1）→ 恐慌预期
7. SOX 涨幅 / 连涨天数 → 半导体极端共识

四、信用/流动性
8. HY-IG 利差走阔 → 信用风险传导
9. 美联储缩表速度（WALCL 变化）→ 流动性收紧

五、情绪/资金流
10. AAII 看涨/看空差（手工更新）
"""

from __future__ import annotations

import io
import json
import logging
import os
import requests
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from data_source.dp_cycle._http import call_with_retry, ensure_http

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# FRED CSV 直链（无需 API Key）
# ──────────────────────────────────────────────

_FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}&coed={end}"


def _fetch_fred_csv(series_id: str, start: str = "2000-01-01") -> pd.DataFrame:
    """从 FRED 公开 CSV 端点拉取数据，返回 DataFrame(date, value)。"""
    end = datetime.now().strftime("%Y-%m-%d")
    url = _FRED_CSV_URL.format(series_id=series_id, start=start, end=end)
    try:
        resp = requests.get(url, timeout=(5, 20))
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        date_column = "observation_date" if "observation_date" in df.columns else "DATE"
        df = df[[date_column, series_id]].copy()
        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["date", "value"]).sort_values("date").reset_index(drop=True)
        df = df[df["date"] <= pd.Timestamp(end)].reset_index(drop=True)
        df.attrs = {
            "source": f"https://fred.stlouisfed.org/series/{series_id}",
            "retrieved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "available_at": None,
        }
        return df
    except Exception as exc:
        logger.warning(f"FRED {series_id} 获取失败: {exc}")
        return pd.DataFrame(columns=["date", "value"])


# ──────────────────────────────────────────────
# 1. VIX 恐慌指数
# ──────────────────────────────────────────────

def fetch_vix(start: str = "2000-01-01") -> pd.DataFrame:
    """获取 VIX 日度序列（FRED VIXCLS）。"""
    return _fetch_fred_csv("VIXCLS", start=start)


# ──────────────────────────────────────────────
# 2. M2 货币供应
# ──────────────────────────────────────────────

def fetch_m2(start: str = "2000-01-01") -> pd.DataFrame:
    """获取美国 M2 货币供应（FRED M2SL，十亿美元，月度）。"""
    return _fetch_fred_csv("M2SL", start=start)


# ──────────────────────────────────────────────
# 3. SOX 费城半导体指数
# ──────────────────────────────────────────────

def fetch_sox() -> pd.DataFrame:
    """获取 SOX 费城半导体指数（akshare，日度）。"""
    ensure_http()
    try:
        raw = call_with_retry(_ak_sox)
        df = raw[["日期", "最新值"]].copy()
        df.columns = ["date", "close"]
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna().sort_values("date").reset_index(drop=True)
        df.attrs["retrieved_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        return df
    except Exception as exc:
        logger.warning(f"SOX 获取失败: {exc}")
        return pd.DataFrame(columns=["date", "close"])


def _ak_sox():
    import akshare as ak
    return ak.macro_global_sox_index()


# ──────────────────────────────────────────────
# 4. S&P 500 / Nasdaq
# ──────────────────────────────────────────────

def fetch_us_index(symbol: str = ".INX") -> pd.DataFrame:
    """获取美股指数日 K（akshare 新浪源）。
    symbol: '.INX'=S&P500, '.IXIC'=Nasdaq, '.DJI'=道琼斯
    """
    ensure_http()
    try:
        raw = call_with_retry(_ak_us_index, symbol)
        df = raw[["date", "close", "volume"]].copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        df = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
        df.attrs["retrieved_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        return df
    except Exception as exc:
        logger.warning(f"US index {symbol} 获取失败: {exc}")
        return pd.DataFrame(columns=["date", "close", "volume"])


def _ak_us_index(symbol: str):
    import akshare as ak
    return ak.index_us_stock_sina(symbol=symbol)


# ──────────────────────────────────────────────
# 5. FINRA 保证金债务
# ──────────────────────────────────────────────

_FINRA_EXCEL_URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
_FINRA_DEBT_COLUMN = "Debit Balances in Customers' Securities Margin Accounts"


def fetch_finra_margin_debt() -> pd.DataFrame:
    """获取 FINRA 保证金债务月度序列。

    仅使用官方 Excel；旧的手工缓存来源无法核实，不参与计算。
    date 为观察月份，retrieved_at 为本次获取时间，available_at 未知。
    返回的债务单位为十亿美元，不能用此表还原历史公布版本。
    """
    columns = ["date", "margin_debt_bn", "source", "retrieved_at", "available_at"]
    try:
        resp = requests.get(_FINRA_EXCEL_URL, timeout=(5, 20))
        resp.raise_for_status()
        raw = pd.read_excel(io.BytesIO(resp.content), sheet_name="Customer Margin Balances")
        result = pd.DataFrame({
            "date": pd.to_datetime(raw["Year-Month"], format="%Y-%m", errors="coerce"),
            "margin_debt_bn": pd.to_numeric(raw[_FINRA_DEBT_COLUMN], errors="coerce") / 1000,
        }).dropna()
        # 月份尚未结束时不能把它当成已公布的月末余额。
        result = result[result["date"].dt.to_period("M") < pd.Timestamp.now().to_period("M")]
        result["source"] = _FINRA_EXCEL_URL
        result["retrieved_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        result["available_at"] = None
        return result[columns].sort_values("date").reset_index(drop=True)
    except Exception as exc:
        logger.warning("FINRA 保证金债务获取失败: %s", exc)
        return pd.DataFrame(columns=columns)


def update_finra_cache(year_month: str, margin_debt_bn: float) -> None:
    """保存手工记录，记录来源未经核实，不参与官方事实序列或评分。

    Args:
        year_month: "YYYY-MM" 格式
        margin_debt_bn: 保证金债务（十亿美元）
    """
    cache_path = _finra_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cached: dict[str, float] = {}
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cached = {}
    cached[year_month] = margin_debt_bn
    cache_path.write_text(json.dumps(cached, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"FINRA 保证金债务已更新: {year_month} = {margin_debt_bn}B")


def _finra_cache_path() -> Path:
    data_dir = Path(__file__).resolve().parents[2] / "data" / "us_crowding"
    return data_dir / "finra_margin_debt.json"


# ──────────────────────────────────────────────
# 6. Shiller CAPE（席勒周期调整市盈率）
# ──────────────────────────────────────────────

_SHILLER_CAPE_URL = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"


def fetch_shiller_cape() -> pd.DataFrame:
    """获取 Shiller CAPE 月度序列（Yale 大学 XLS）。

    返回 DataFrame(date, cape) — cape 为周期调整市盈率。
    """
    try:
        import requests
        resp = requests.get(_SHILLER_CAPE_URL, timeout=20)
        resp.raise_for_status()
        df = pd.read_excel(io.BytesIO(resp.content), sheet_name="Data", skiprows=7)
        # CAPE 列直接存在
        if "CAPE" not in df.columns:
            logger.warning("Shiller CAPE: 未找到 CAPE 列")
            return pd.DataFrame(columns=["date", "cape"])

        # Date 列为 YYYY.MM 格式的浮点数
        date_raw = df["Date"]
        cape_raw = pd.to_numeric(df["CAPE"], errors="coerce")

        # 构建结果
        result = pd.DataFrame({"date_raw": date_raw, "cape": cape_raw})
        result = result.dropna(subset=["cape"])

        # 解析日期：YYYY.MM -> datetime
        def _parse_date(val):
            try:
                # Excel 的 2023.10 读为浮点数 2023.1，仍代表十月。
                y, m = f"{float(val):.2f}".split(".")
                return pd.Timestamp(year=int(y), month=int(m), day=1)
            except Exception:
                pass
            return pd.NaT

        result["date"] = result["date_raw"].apply(_parse_date)
        result = result.dropna(subset=["date"])[["date", "cape"]]
        result = result.sort_values("date").reset_index(drop=True)
        result.attrs["retrieved_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        return result
    except Exception as exc:
        logger.warning(f"Shiller CAPE 获取失败: {exc}")
        return pd.DataFrame(columns=["date", "cape"])


# ──────────────────────────────────────────────
# 7. VIX 期限结构（VIX vs VXV）
# ──────────────────────────────────────────────

def fetch_vxv(start: str = "2010-01-01") -> pd.DataFrame:
    """获取 CBOE 3 个月 VIX (VXV) 日度序列（FRED VXVCLS）。"""
    return _fetch_fred_csv("VXVCLS", start=start)


def compute_vix_term_structure() -> dict[str, Any]:
    """计算 VIX 期限结构。

    Returns:
        dict with keys: vix, vxv, ratio, structure ('contango'/'backwardation')
    """
    vix_df = fetch_vix(start="2020-01-01")
    vxv_df = fetch_vxv(start="2020-01-01")
    if vix_df.empty or vxv_df.empty:
        return {}
    vix_s = vix_df.set_index("date")["value"]
    vxv_s = vxv_df.set_index("date")["value"]
    common = vix_s.index.intersection(vxv_s.index)
    if len(common) == 0:
        return {}
    latest = common[-1]
    v = float(vix_s[latest])
    v3 = float(vxv_s[latest])
    ratio = v / v3 if v3 > 0 else None
    return {
        "vix": v,
        "vxv": v3,
        "ratio": round(ratio, 4) if ratio else None,
        "structure": "backwardation" if ratio and ratio > 1 else "contango",
        "date": str(latest.date()) if hasattr(latest, "date") else str(latest),
    }


# ──────────────────────────────────────────────
# 8. 信用利差（HY / IG）
# ──────────────────────────────────────────────

def fetch_hy_spread(start: str = "2010-01-01") -> pd.DataFrame:
    """获取美国高收益债 OAS（FRED BAMLH0A0HYM2，百分比）。"""
    return _fetch_fred_csv("BAMLH0A0HYM2", start=start)


def fetch_ig_spread(start: str = "2010-01-01") -> pd.DataFrame:
    """获取美国 BBB 级债 OAS（FRED BAMLC0A4CBBB，百分比）。"""
    return _fetch_fred_csv("BAMLC0A4CBBB", start=start)


def compute_credit_stress() -> dict[str, Any]:
    """计算信用压力指标。

    Returns:
        dict with keys: hy, ig, spread, hy_pct, ig_pct
    """
    hy_df = fetch_hy_spread(start="2014-01-01")
    ig_df = fetch_ig_spread(start="2014-01-01")
    if hy_df.empty or ig_df.empty:
        return {}
    hy_s = hy_df.set_index("date")["value"].sort_index()
    ig_s = ig_df.set_index("date")["value"].sort_index()
    common = hy_s.index.intersection(ig_s.index).sort_values()
    if len(common) == 0:
        return {}
    latest = common[-1]
    # 只比较截至最新共同观测日已经存在的样本。
    hy_s = hy_s.loc[:latest]
    ig_s = ig_s.loc[:latest]
    hy_val = float(hy_s[latest])
    ig_val = float(ig_s[latest])
    spread = hy_val - ig_val
    # 历史分位
    all_spread = (hy_s - ig_s).dropna()
    hy_pct = float((hy_s < hy_val).mean() * 100)
    ig_pct = float((ig_s < ig_val).mean() * 100)
    spread_pct = float((all_spread < spread).mean() * 100)
    histories = {
        name: {"start_date": str(series.index[0].date()),
               "end_date": str(series.index[-1].date()), "count": len(series)}
        for name, series in (("hy_history", hy_s), ("ig_history", ig_s), ("spread_history", all_spread))
    }
    return {
        "hy": round(hy_val, 2),
        "ig": round(ig_val, 2),
        "spread": round(spread, 2),
        "hy_pct": round(hy_pct, 1),
        "ig_pct": round(ig_pct, 1),
        "bbb": round(ig_val, 2),
        "bbb_pct": round(ig_pct, 1),
        "spread_pct": round(spread_pct, 1),
        "spread_pct_unrounded": spread_pct,
        "unit": "percent",
        "spread_unit": "percentage_points",
        "source": {
            "hy": hy_df.attrs.get("source", "https://fred.stlouisfed.org/series/BAMLH0A0HYM2"),
            "bbb": ig_df.attrs.get("source", "https://fred.stlouisfed.org/series/BAMLC0A4CBBB"),
        },
        "retrieved_at": {"hy": hy_df.attrs.get("retrieved_at"), "bbb": ig_df.attrs.get("retrieved_at")},
        "available_at": None,
        **histories,
        "date": str(latest.date()) if hasattr(latest, "date") else str(latest),
    }


# ──────────────────────────────────────────────
# 9. 美联储流动性（资产负债表 + 逆回购）
# ──────────────────────────────────────────────

def fetch_fed_assets(start: str = "2015-01-01") -> pd.DataFrame:
    """获取美联储总资产（FRED WALCL，百万美元，周度）。"""
    return _fetch_fred_csv("WALCL", start=start)


def fetch_fed_treasury(start: str = "2015-01-01") -> pd.DataFrame:
    """获取美联储持有国债（FRED TREAST，百万美元，周度）。"""
    return _fetch_fred_csv("TREAST", start=start)


def fetch_on_rrp(start: str = "2015-01-01") -> pd.DataFrame:
    """获取隔夜逆回购（FRED RRPONTSYD，十亿美元，日度）。"""
    return _fetch_fred_csv("RRPONTSYD", start=start)


def compute_net_liquidity() -> dict[str, Any]:
    """获取美联储总资产、13 周变化和隔夜逆回购。

    此函数未获取 TGA，也未计算资产减 TGA 和逆回购的净流动性。
    """
    walcl_df = fetch_fed_assets(start="2020-01-01")
    rrp_df = fetch_on_rrp(start="2020-01-01")
    if walcl_df.empty:
        return {}
    result: dict[str, Any] = {}
    latest_walcl = float(walcl_df["value"].iloc[-1])
    result["fed_assets_bn"] = round(latest_walcl / 1000, 1)  # 转为十亿
    result["fed_assets_date"] = str(walcl_df["date"].iloc[-1].date()) if hasattr(walcl_df["date"].iloc[-1], "date") else str(walcl_df["date"].iloc[-1])
    # 缩表速度：13周变化率
    if len(walcl_df) >= 14:
        prev = float(walcl_df["value"].iloc[-14])
        if prev > 0:
            result["qt_13w_pct"] = round((latest_walcl / prev - 1) * 100, 2)
    # 逆回购
    if not rrp_df.empty:
        result["on_rrp_bn"] = round(float(rrp_df["value"].iloc[-1]), 2)
        result["on_rrp_date"] = str(rrp_df["date"].iloc[-1].date()) if hasattr(rrp_df["date"].iloc[-1], "date") else str(rrp_df["date"].iloc[-1])
    return result


# ──────────────────────────────────────────────
# 10. 国债收益率 / 利率
# ──────────────────────────────────────────────

def fetch_yields() -> dict[str, Any]:
    """获取关键国债收益率。"""
    result: dict[str, Any] = {}
    for sid, key in [("DGS10", "y10"), ("DGS2", "y2"), ("DTB3", "tbill_3m")]:
        df = _fetch_fred_csv(sid, start="2024-01-01")
        if not df.empty:
            result[key] = round(float(df["value"].iloc[-1]), 2)
            result[f"{key}_date"] = str(df["date"].iloc[-1].date()) if hasattr(df["date"].iloc[-1], "date") else str(df["date"].iloc[-1])
    # 2s10s 利差
    if "y10" in result and "y2" in result:
        result["spread_2s10s"] = round(result["y10"] - result["y2"], 2)
    # 3m-10s 利差
    if "y10" in result and "tbill_3m" in result:
        result["spread_3m10s"] = round(result["y10"] - result["tbill_3m"], 2)
    return result


# ──────────────────────────────────────────────
# 11. GDP（用于巴菲特指标等）
# ──────────────────────────────────────────────

def fetch_gdp() -> pd.DataFrame:
    """获取美国名义 GDP（FRED GDP，十亿美元，季度）。"""
    return _fetch_fred_csv("GDP", start="2000-01-01")


# ──────────────────────────────────────────────
# 12. 综合拥挤度指标
# ──────────────────────────────────────────────

@dataclass
class USCrowdingSnapshot:
    """美股拥挤度快照。"""
    as_of: str = ""
    generated_at: str = ""
    schema_version: int = 2

    # ── 杠杆/债务 ──
    vix_last: float | None = None
    vix_pct: float | None = None          # 历史分位 (0-100)
    spx_last: float | None = None
    spx_ytd_pct: float | None = None      # 年初至今涨幅分位
    sox_last: float | None = None
    sox_60d_ret: float | None = None      # 60日涨幅 %
    sox_pct: float | None = None          # 250日历史分位
    margin_debt_bn: float | None = None   # 最新保证金债务（十亿美元）
    m2_bn: float | None = None            # 最新 M2（十亿美元）
    margin_m2_debt_bn: float | None = None # 与 M2 同月的保证金债务
    margin_m2_m2_bn: float | None = None   # 与保证金债务同月的 M2
    margin_m2_pct: float | None = None    # 保证金债务/M2 × 100
    gdp_bn: float | None = None           # 最新 GDP（十亿美元）
    margin_gdp_pct: float | None = None   # 保证金债务/GDP × 100

    # ── 估值 ──
    shiller_cape: float | None = None     # 席勒 CAPE
    cape_pct: float | None = None         # CAPE 历史分位 (0-100)

    # ── 波动率结构 ──
    vxv_last: float | None = None         # 3月 VIX
    vix_vxv_ratio: float | None = None    # VIX/VXV 期限结构
    vix_structure: str = ""               # 'contango' / 'backwardation'

    # ── 信用 ──
    hy_spread: float | None = None        # HY 利差
    ig_spread: float | None = None        # IG 利差
    hy_ig_spread: float | None = None     # HY-IG 利差
    hy_ig_pct: float | None = None        # HY-IG 历史分位

    # ── 流动性 ──
    fed_assets_bn: float | None = None    # 美联储总资产（十亿）
    qt_13w_pct: float | None = None       # 13周缩表速度 %
    on_rrp_bn: float | None = None        # 逆回购（十亿）

    # ── 利率 ──
    y10: float | None = None
    y2: float | None = None
    tbill_3m: float | None = None
    spread_2s10s: float | None = None     # 2s10s 利差

    # ── 预警 ──
    margin_m2_danger: bool = False        # ≥ 6% 为危险
    vix_elevated: bool = False            # 分位 ≥ 60%
    sox_overbought: bool = False          # 60日涨幅 > 30%
    cape_extreme: bool = False            # CAPE ≥ 35
    credit_stress: bool = False           # HY-IG 分位 ≥ 80%

    # ── 综合评分 ──
    crowding_score: int | None = None     # 仅五项均可评估时计算（0 ~ 5）
    evaluable_count: int = 0
    triggered_count: int = 0
    component_status: dict[str, Any] = field(default_factory=dict)
    verdict: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


_SNAPSHOT_CACHE_NAME = "us_crowding_snapshot.json"
_SNAPSHOT_TTL_HOURS = 24
_SCORING_FLAGS = {
    "vix": "vix_elevated", "sox": "sox_overbought", "margin_m2": "margin_m2_danger",
    "cape": "cape_extreme", "credit": "credit_stress",
}


def _snapshot_cache_path() -> Path:
    try:
        from output_paths import dp_cycle_store_dir

        return Path(dp_cycle_store_dir()) / _SNAPSHOT_CACHE_NAME
    except Exception:
        return Path(__file__).resolve().parents[2] / "data" / "us_crowding" / _SNAPSHOT_CACHE_NAME


def load_cached_crowding_snapshot(
    *, max_age_hours: float = _SNAPSHOT_TTL_HOURS
) -> dict[str, Any] | None:
    """读本地拥挤度快照；过期或不存在返回 None。"""
    path = _snapshot_cache_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        # 旧快照可能含未经核实的手工数据，不能参与当前判断。
        if raw.get("schema_version") != 2 or not isinstance(raw.get("component_status"), dict):
            return None
        cached_at = raw.get("_cached_at") or raw.get("as_of")
        if not cached_at:
            return None
        ts = datetime.fromisoformat(str(cached_at)[:19])
        age_h = (datetime.now() - ts).total_seconds() / 3600.0
        if age_h > float(max_age_hours):
            return None
        aged_out = False
        for item in raw["component_status"].values():
            observed, max_age = item.get("observation_date"), item.get("max_age_days")
            if item.get("status") == "available" and observed and max_age is not None:
                age_days = (pd.Timestamp.now().normalize() - pd.Timestamp(observed)).days
                if age_days < 0 or age_days > max_age:
                    item["status"] = "future" if age_days < 0 else "stale"
                    aged_out = True
        if aged_out:
            components = raw["component_status"]
            for name in ("finra_margin", "gdp"):
                if components.get(name, {}).get("status") != "available":
                    components.get("margin_gdp", {})["status"] = components.get(name, {}).get("status", "missing")
                    break
            for name, flag in _SCORING_FLAGS.items():
                if components.get(name, {}).get("status") != "available":
                    raw[flag] = False
            count = sum(components.get(name, {}).get("status") == "available" for name in _SCORING_FLAGS)
            triggered = sum(bool(raw.get(flag)) for flag in _SCORING_FLAGS.values())
            raw["evaluable_count"], raw["triggered_count"] = count, triggered
            raw["crowding_score"] = triggered if count == len(_SCORING_FLAGS) else None
            if count < len(_SCORING_FLAGS):
                raw["verdict"] = f"数据不足：可评估 {count}/5 项，{triggered} 项触发预警"
        raw.pop("_cached_at", None)
        raw["cache_at"] = cached_at
        raw["cache_age_hours"] = round(age_h, 2)
        raw["cache_state"] = "stale" if age_h > _SNAPSHOT_TTL_HOURS else "cached"
        raw["from_stale_cache"] = raw["cache_state"] == "stale"
        return raw
    except Exception as exc:
        logger.warning(f"读取美股拥挤度缓存失败: {exc}")
        return None


def save_crowding_snapshot_cache(data: dict[str, Any]) -> None:
    path = _snapshot_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload["_cached_at"] = datetime.now().isoformat(timespec="seconds")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"写入美股拥挤度缓存失败: {exc}")


def load_or_compute_crowding_snapshot(
    *,
    force_refresh: bool = False,
    max_age_hours: float = _SNAPSHOT_TTL_HOURS,
) -> dict[str, Any] | None:
    """读库场景优先用本地快照；更新入库或缓存过期才联网重算。"""
    if not force_refresh:
        cached = load_cached_crowding_snapshot(max_age_hours=max_age_hours)
        if cached is not None:
            return cached
    attempted_at = datetime.now().astimezone().isoformat(timespec="seconds")
    observed_fields: list[str] = []
    refresh_components: dict[str, Any] = {}
    refresh_evaluable_count = 0
    missing_prior_items: list[str] = []
    regressed_prior_items: list[str] = []
    cached = None
    try:
        snap = compute_crowding_snapshot()
        data = snap.to_dict()
        observed = (
            "vix_last", "spx_last", "sox_last", "margin_debt_bn", "m2_bn", "gdp_bn",
            "shiller_cape", "vxv_last", "hy_spread", "ig_spread", "fed_assets_bn",
            "on_rrp_bn", "y10", "y2", "tbill_3m",
        )
        observed_fields = [name for name in observed if data.get(name) is not None]
        refresh_evaluable_count = data["evaluable_count"]
        refresh_components = data["component_status"]
        cached = load_cached_crowding_snapshot(max_age_hours=float("inf"))
        if cached is not None:
            prior_components = cached["component_status"]
            missing_prior_items = [
                name for name in _SCORING_FLAGS
                if prior_components.get(name, {}).get("status") == "available"
                and refresh_components.get(name, {}).get("status") != "available"
            ]
            regressed_prior_items = [
                name for name in _SCORING_FLAGS
                if prior_components.get(name, {}).get("status") == "available"
                and refresh_components.get(name, {}).get("status") == "available"
                and pd.Timestamp(refresh_components[name]["observation_date"])
                    < pd.Timestamp(prior_components[name]["observation_date"])
            ]
        if refresh_evaluable_count:
            if not missing_prior_items and not regressed_prior_items:
                data.update(refresh_failed=False, refresh_attempted_at=attempted_at,
                            refresh_status="complete" if refresh_evaluable_count == 5 else "partial",
                            refresh_evaluable_count=refresh_evaluable_count)
                save_crowding_snapshot_cache(data)
                return data
            reasons = []
            if missing_prior_items:
                reasons.append("本次缺少旧快照仍有效的评分项：" + "、".join(missing_prior_items))
            if regressed_prior_items:
                reasons.append("本次观察日期早于旧快照：" + "、".join(regressed_prior_items))
            reason = "；".join(reasons)
        else:
            reason = "本次取得部分观测，但没有可评估的评分项" if observed_fields else "本次刷新未取得外盘观测"
    except Exception as exc:
        logger.warning(f"计算美股拥挤度失败: {exc}")
        data = USCrowdingSnapshot(as_of=datetime.now().strftime("%Y-%m-%d"), generated_at=attempted_at).to_dict()
        reason = "本次刷新发生计算异常"
    # 缺少有效评分项或日期倒退时不覆盖旧快照，新观测状态单独列出。
    if cached is None:
        cached = load_cached_crowding_snapshot(max_age_hours=float("inf"))
    if cached is not None:
        data = cached
    else:
        data["cache_state"] = "unavailable"
    data.update(refresh_failed=True, refresh_attempted_at=attempted_at, refresh_error=reason,
                refresh_status="partial" if observed_fields else "failed",
                refresh_evaluable_count=refresh_evaluable_count,
                refresh_missing_prior_items=missing_prior_items,
                refresh_regressed_prior_items=regressed_prior_items,
                refresh_observed_fields=observed_fields, refresh_component_status=refresh_components)
    return data


def compute_crowding_snapshot() -> USCrowdingSnapshot:
    """计算当前美股拥挤度快照（会联网；GUI 读库请用 load_or_compute_crowding_snapshot）。"""
    snap = USCrowdingSnapshot()
    snap.as_of = datetime.now().strftime("%Y-%m-%d")
    snap.generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    # --- VIX ---
    vix_df = fetch_vix(start="2004-01-01")  # VIX 从 2004 开始
    if not vix_df.empty:
        snap.vix_last = float(vix_df["value"].iloc[-1])
        # 250日滚动分位
        window = vix_df.tail(252)
        if len(window) >= 60:
            snap.vix_pct = float((window["value"] < snap.vix_last).mean() * 100)
        if snap.vix_pct is not None and snap.vix_pct >= 60:
            snap.vix_elevated = True

    # --- S&P 500 ---
    spx_df = fetch_us_index(".INX")
    if not spx_df.empty:
        snap.spx_last = float(spx_df["close"].iloc[-1])
        # 250日动量分位
        if len(spx_df) >= 252:
            recent = spx_df.tail(252)
            snap.spx_ytd_pct = float(
                (recent["close"] < snap.spx_last).mean() * 100
            )

    # --- SOX ---
    sox_df = fetch_sox()
    if not sox_df.empty:
        snap.sox_last = float(sox_df["close"].iloc[-1])
        if len(sox_df) >= 61:
            v60 = float(sox_df["close"].iloc[-61])
            snap.sox_60d_ret = float((snap.sox_last / v60 - 1) * 100)
        if len(sox_df) >= 252:
            recent = sox_df.tail(252)
            snap.sox_pct = float(
                (recent["close"] < snap.sox_last).mean() * 100
            )
        if snap.sox_60d_ret is not None and snap.sox_60d_ret > 30:
            snap.sox_overbought = True

    # --- M2 ---
    m2_df = fetch_m2(start="2010-01-01")
    if not m2_df.empty:
        snap.m2_bn = float(m2_df["value"].iloc[-1])  # 十亿美元

    # --- FINRA 保证金债务 ---
    finra_df = fetch_finra_margin_debt()
    if not finra_df.empty:
        snap.margin_debt_bn = float(finra_df["margin_debt_bn"].iloc[-1])

    # --- 保证金债务 / M2 ---
    margin_m2 = compute_margin_m2_series(finra_df, m2_df)
    if not margin_m2.empty:
        matched = margin_m2.iloc[-1]
        snap.margin_m2_debt_bn = float(matched["margin_debt_bn"])
        snap.margin_m2_m2_bn = float(matched["m2_bn"])
        snap.margin_m2_pct = float(matched["margin_m2_pct"])
        if snap.margin_m2_pct >= 6.0:
            snap.margin_m2_danger = True

    # --- 保证金债务 / GDP ---
    gdp_df = fetch_gdp()
    if not gdp_df.empty:
        snap.gdp_bn = float(gdp_df["value"].iloc[-1])
    if snap.margin_debt_bn is not None and snap.gdp_bn is not None and snap.gdp_bn > 0:
        snap.margin_gdp_pct = float(snap.margin_debt_bn / snap.gdp_bn * 100)

    # --- Shiller CAPE ---
    cape_df = fetch_shiller_cape()
    if not cape_df.empty:
        snap.shiller_cape = float(cape_df["cape"].iloc[-1])
        if len(cape_df) >= 120:
            snap.cape_pct = float((cape_df["cape"] < snap.shiller_cape).mean() * 100)
        if snap.shiller_cape >= 35:
            snap.cape_extreme = True

    # --- VIX 期限结构 ---
    term = compute_vix_term_structure()
    if term:
        snap.vxv_last = term.get("vxv")
        snap.vix_vxv_ratio = term.get("ratio")
        snap.vix_structure = term.get("structure", "")

    # --- 信用利差 ---
    credit = compute_credit_stress()
    if credit:
        snap.hy_spread = credit.get("hy")
        snap.ig_spread = credit.get("ig")
        snap.hy_ig_spread = credit.get("spread")
        snap.hy_ig_pct = credit.get("spread_pct_unrounded", credit.get("spread_pct"))
        if snap.hy_ig_pct is not None and snap.hy_ig_pct >= 80:
            snap.credit_stress = True

    # --- 流动性 ---
    liq = compute_net_liquidity()
    if liq:
        snap.fed_assets_bn = liq.get("fed_assets_bn")
        snap.qt_13w_pct = liq.get("qt_13w_pct")
        snap.on_rrp_bn = liq.get("on_rrp_bn")

    # --- 利率 ---
    yields = fetch_yields()
    if yields:
        snap.y10 = yields.get("y10")
        snap.y2 = yields.get("y2")
        snap.tbill_3m = yields.get("tbill_3m")
        snap.spread_2s10s = yields.get("spread_2s10s")

    # --- 综合评分 ---
    def last_date(data: pd.DataFrame) -> str | None:
        return str(pd.Timestamp(data["date"].iloc[-1]).date()) if not data.empty else None

    def status(value, observed, source, unit, retrieved=None, max_age_days=None, **details):
        state = "missing" if value is None or pd.isna(value) or not observed else "available"
        if state == "available" and max_age_days is not None:
            age = (pd.Timestamp(snap.as_of) - pd.Timestamp(observed)).days
            if age < 0:
                state = "future"
            elif age > max_age_days:
                state = "stale"
        return {
            "status": state,
            "observation_date": observed, "source": source, "unit": unit,
            "available_at": None, "retrieved_at": retrieved, "max_age_days": max_age_days, **details,
        }

    finra_source = _FINRA_EXCEL_URL
    m2_source = "https://fred.stlouisfed.org/series/M2SL"
    finra_retrieved = finra_df["retrieved_at"].iloc[-1] if not finra_df.empty and "retrieved_at" in finra_df else None
    m2_retrieved = m2_df.attrs.get("retrieved_at")
    margin_date = last_date(margin_m2)
    snap.component_status = {
        "vix": status(snap.vix_pct, last_date(vix_df), vix_df.attrs.get("source", "https://fred.stlouisfed.org/series/VIXCLS"),
                      "percentile", vix_df.attrs.get("retrieved_at"), max_age_days=14, sample_size=min(len(vix_df), 252)),
        "spx": status(snap.spx_last, last_date(spx_df), "akshare.index_us_stock_sina", "index points",
                      spx_df.attrs.get("retrieved_at"), max_age_days=14),
        "sox": status(snap.sox_60d_ret, last_date(sox_df), "akshare.macro_global_sox_index", "percent",
                      sox_df.attrs.get("retrieved_at"), max_age_days=14),
        "finra_margin": status(snap.margin_debt_bn, last_date(finra_df), finra_source, "USD billions", finra_retrieved, max_age_days=120),
        "m2": status(snap.m2_bn, last_date(m2_df), m2_source, "USD billions", m2_retrieved, max_age_days=120),
        "margin_m2": status(snap.margin_m2_pct, margin_date, [finra_source, m2_source], "percent",
                            {"finra": finra_retrieved, "m2": m2_retrieved}, max_age_days=120),
        "gdp": status(snap.gdp_bn, last_date(gdp_df), "https://fred.stlouisfed.org/series/GDP",
                      "USD billions, annualized", gdp_df.attrs.get("retrieved_at"), max_age_days=180,
                      quarter=str(pd.Timestamp(gdp_df["date"].iloc[-1]).to_period("Q")) if not gdp_df.empty else None),
        "margin_gdp": status(snap.margin_gdp_pct, last_date(finra_df), [finra_source, "https://fred.stlouisfed.org/series/GDP"],
                             "percent", {"finra": finra_retrieved, "gdp": gdp_df.attrs.get("retrieved_at")},
                             gdp_observation_date=last_date(gdp_df)),
        "cape": status(snap.shiller_cape, last_date(cape_df), _SHILLER_CAPE_URL, "ratio", cape_df.attrs.get("retrieved_at"), max_age_days=120),
        "credit": status(snap.hy_ig_pct, credit.get("date"), credit.get("source", {}), "percentile",
                         credit.get("retrieved_at"), max_age_days=14, spread_unit="percentage_points",
                         history=credit.get("spread_history"), hy_history=credit.get("hy_history"),
                         bbb_history=credit.get("ig_history")),
    }
    for name in ("finra_margin", "gdp"):
        if snap.component_status[name]["status"] != "available":
            snap.component_status["margin_gdp"]["status"] = snap.component_status[name]["status"]
            break
    snap.evaluable_count = sum(snap.component_status[name]["status"] == "available" for name in _SCORING_FLAGS)
    for name, flag in _SCORING_FLAGS.items():
        if snap.component_status[name]["status"] != "available":
            setattr(snap, flag, False)
    score = sum(getattr(snap, flag) for flag in _SCORING_FLAGS.values())
    snap.triggered_count = score
    snap.crowding_score = score if snap.evaluable_count == 5 else None
    if snap.evaluable_count < 5:
        snap.verdict = f"数据不足：可评估 {snap.evaluable_count}/5 项，{score} 项触发预警"
    elif score >= 5:
        snap.verdict = "极度拥挤：多项指标同时亮红灯，历史级风险"
    elif score >= 3:
        snap.verdict = "高度拥挤：多项指标预警，警惕回调"
    elif score >= 2:
        snap.verdict = "偏热：两项指标预警，注意仓位"
    elif score >= 1:
        snap.verdict = "微热：一项指标预警，保持关注"
    else:
        snap.verdict = "正常：拥挤度指标未触发预警"

    return snap


# ──────────────────────────────────────────────
# 7. 批量获取 + 存储
# ──────────────────────────────────────────────

def fetch_all_us_crowding() -> dict[str, pd.DataFrame]:
    """获取所有美股拥挤度相关数据。"""
    result: dict[str, pd.DataFrame] = {}
    result["vix"] = fetch_vix()
    result["vxv"] = fetch_vxv()
    result["m2"] = fetch_m2()
    result["gdp"] = fetch_gdp()
    result["sox"] = fetch_sox()
    result["spx"] = fetch_us_index(".INX")
    result["ndx"] = fetch_us_index(".IXIC")
    result["finra_margin"] = fetch_finra_margin_debt()
    result["shiller_cape"] = fetch_shiller_cape()
    result["hy_spread"] = fetch_hy_spread()
    result["ig_spread"] = fetch_ig_spread()
    result["fed_assets"] = fetch_fed_assets()
    result["on_rrp"] = fetch_on_rrp()
    return result


def compute_margin_m2_series(
    finra_df: pd.DataFrame, m2_df: pd.DataFrame
) -> pd.DataFrame:
    """计算保证金债务/M2 比值的月度序列。

    此序列使用本次可取得的历史值，不等于历史上各月当时可知的版本。

    Returns:
        DataFrame(date, margin_debt_bn, m2_bn, margin_m2_pct)
    """
    if finra_df.empty or m2_df.empty:
        return pd.DataFrame(columns=["date", "margin_debt_bn", "m2_bn", "margin_m2_pct"])

    f = finra_df.copy()
    m = m2_df.copy()
    for data in (f, m):
        data["date"] = pd.to_datetime(data["date"]).dt.to_period("M").dt.to_timestamp()
        if "available_at" in data:
            available = pd.to_datetime(data["available_at"], utc=True, errors="coerce")
            data.drop(data.index[available.notna() & (available > pd.Timestamp.now(tz="UTC"))], inplace=True)
        # 不能使用尚未结束的月度观察值。
        data.drop(data.index[data["date"] >= pd.Timestamp.now().to_period("M").start_time], inplace=True)
    # FINRA 和 M2 必须属于同一观察月份，不能沿用更早的 M2 冒充当月值。
    merged = f.merge(m.rename(columns={"value": "m2_bn"}), on="date", how="inner", suffixes=("_finra", "_m2"))
    merged = merged.dropna(subset=["margin_debt_bn", "m2_bn"])
    merged = merged[merged["m2_bn"] > 0].sort_values("date")
    merged["margin_m2_pct"] = merged["margin_debt_bn"] / merged["m2_bn"] * 100
    return merged[["date", "margin_debt_bn", "m2_bn", "margin_m2_pct"]].reset_index(drop=True)


# ──────────────────────────────────────────────
# CLI 快速测试
# ──────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=" * 60)
    print("美股拥挤度全景快照")
    print("=" * 60)
    snap = compute_crowding_snapshot()
    d = snap.to_dict()

    print("\n【杠杆/债务】")
    print(f"  保证金债务: {d.get('margin_debt_bn', 'N/A')}B")
    print(f"  M2: {d.get('m2_bn', 'N/A')}B")
    print(f"  保证金/M2: {d.get('margin_m2_pct', 'N/A')}% {'⚠️ 危险' if d.get('margin_m2_danger') else ''}")
    print(f"  GDP: {d.get('gdp_bn', 'N/A')}B")
    print(f"  保证金/GDP: {d.get('margin_gdp_pct', 'N/A')}%")

    print("\n【估值】")
    print(f"  Shiller CAPE: {d.get('shiller_cape', 'N/A')} (分位 {d.get('cape_pct', 'N/A')}%) {'⚠️ 极端' if d.get('cape_extreme') else ''}")

    print("\n【波动率/衍生品】")
    print(f"  VIX: {d.get('vix_last', 'N/A')} (分位 {d.get('vix_pct', 'N/A')}%) {'⚠️ 偏高' if d.get('vix_elevated') else ''}")
    print(f"  VXV(3M): {d.get('vxv_last', 'N/A')}")
    print(f"  VIX/VXV: {d.get('vix_vxv_ratio', 'N/A')} ({d.get('vix_structure', '')})")
    print(f"  SOX: {d.get('sox_last', 'N/A')} (60d涨幅 {d.get('sox_60d_ret', 'N/A')}%) {'⚠️ 过热' if d.get('sox_overbought') else ''}")

    print("\n【信用/流动性】")
    print(f"  HY利差: {d.get('hy_spread', 'N/A')}%")
    print(f"  IG利差: {d.get('ig_spread', 'N/A')}%")
    print(f"  HY-IG: {d.get('hy_ig_spread', 'N/A')}% (分位 {d.get('hy_ig_pct', 'N/A')}%) {'⚠️ 信用压力' if d.get('credit_stress') else ''}")
    print(f"  美联储资产: {d.get('fed_assets_bn', 'N/A')}B (13w变化 {d.get('qt_13w_pct', 'N/A')}%)")
    print(f"  逆回购: {d.get('on_rrp_bn', 'N/A')}B")

    print("\n【利率】")
    print(f"  10Y: {d.get('y10', 'N/A')}%")
    print(f"  2Y: {d.get('y2', 'N/A')}%")
    print(f"  3M T-Bill: {d.get('tbill_3m', 'N/A')}%")
    print(f"  2s10s利差: {d.get('spread_2s10s', 'N/A')}%")

    print(f"\n{'='*60}")
    print(f"综合评分: {d.get('crowding_score')}/5（可评估 {d.get('evaluable_count')}/5 项）")
    print(f"诊断: {d.get('verdict', '')}")
    print(f"{'='*60}")
