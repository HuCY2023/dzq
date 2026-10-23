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
- FINRA 保证金债务：FINRA 官网 / 手工更新

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


def _fetch_fred_csv(series_id: str, start: str = "2000-01-01", skiprows: int = 12) -> pd.DataFrame:
    """从 FRED 公开 CSV 端点拉取数据，返回 DataFrame(date, value)。"""
    import io
    end = datetime.now().strftime("%Y-%m-%d")
    url = _FRED_CSV_URL.format(series_id=series_id, start=start, end=end)
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        df = pd.read_csv(
            io.StringIO(resp.text), skiprows=skiprows, header=None, names=["date", "value"],
        )
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["date", "value"]).sort_values("date").reset_index(drop=True)
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
        return df.dropna().sort_values("date").reset_index(drop=True)
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
        return df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    except Exception as exc:
        logger.warning(f"US index {symbol} 获取失败: {exc}")
        return pd.DataFrame(columns=["date", "close", "volume"])


def _ak_us_index(symbol: str):
    import akshare as ak
    return ak.index_us_stock_sina(symbol=symbol)


# ──────────────────────────────────────────────
# 5. FINRA 保证金债务（手工更新 + 缓存）
# ──────────────────────────────────────────────

# 手工更新区：FINRA 每月公布一次，滞后约 3-4 周
# 来源: https://www.finra.org/investors/learn-to-invest/general-investment-information/margin-statistics
# 单位: 十亿美元
_FINRA_MARGIN_DEBT_MANUAL = {
    # YYYY-MM: 保证金债务（十亿美元）
    "2024-01": 886.0,
    "2024-02": 903.0,
    "2024-03": 926.0,
    "2024-04": 940.0,
    "2024-05": 954.0,
    "2024-06": 972.0,
    "2024-07": 999.0,
    "2024-08": 1020.0,
    "2024-09": 1043.0,
    "2024-10": 1060.0,
    "2024-11": 1085.0,
    "2024-12": 1110.0,
    "2025-01": 1130.0,
    "2025-02": 1155.0,
    "2025-03": 1180.0,
    "2025-04": 1200.0,
    "2025-05": 1230.0,
    "2025-06": 1260.0,
    "2025-07": 1310.0,
    "2025-08": 1350.0,
    "2025-09": 1380.0,
    "2025-10": 1420.0,
    "2025-11": 1440.0,
    "2025-12": 1460.0,
    "2026-01": 1475.0,
    "2026-02": 1490.0,
    "2026-03": 1500.0,
    "2026-04": 1480.0,
    "2026-05": 1420.0,
    "2026-06": 1502.0,
    "2026-07": 1480.0,
    "2026-08": 1454.0,
}

# 更早期的历史关键值（用于分位计算）
_FINRA_HISTORY_ANCHORS = {
    # 泡沫顶部的保证金债务（十亿美元）
    "2000-03": 250.0,   # 科网泡沫顶
    "2007-07": 380.0,   # 次贷危机前
    "2021-10": 650.0,   # 后疫情牛市顶
}


def fetch_finra_margin_debt() -> pd.DataFrame:
    """获取 FINRA 保证金债务月度序列。

    优先读本地缓存 JSON，合并手工更新数据。
    返回 DataFrame(date, margin_debt_bn) — 单位十亿美元。
    """
    cache_path = _finra_cache_path()
    cached: dict[str, float] = {}
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cached = {}

    # 合并手工更新
    merged = {**_FINRA_HISTORY_ANCHORS, **cached, **_FINRA_MARGIN_DEBT_MANUAL}
    if not merged:
        return pd.DataFrame(columns=["date", "margin_debt_bn"])

    rows = []
    for ym, val in sorted(merged.items()):
        try:
            dt = pd.Timestamp(ym + "-01")
            rows.append({"date": dt, "margin_debt_bn": float(val)})
        except Exception:
            continue
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def update_finra_cache(year_month: str, margin_debt_bn: float) -> None:
    """手工更新 FINRA 保证金债务缓存。

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
                s = str(val).strip()
                if "." in s:
                    parts = s.split(".")
                    y = int(parts[0])
                    m = int(parts[1]) if len(parts) > 1 else 1
                    return pd.Timestamp(year=y, month=m, day=1)
            except Exception:
                pass
            return pd.NaT

        result["date"] = result["date_raw"].apply(_parse_date)
        result = result.dropna(subset=["date"])[["date", "cape"]]
        result = result.sort_values("date").reset_index(drop=True)
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
    """获取美国高收益债利差（FRED BAMLH0A0HYM2，基点）。"""
    return _fetch_fred_csv("BAMLH0A0HYM2", start=start)


def fetch_ig_spread(start: str = "2010-01-01") -> pd.DataFrame:
    """获取美国投资级债利差（FRED BAMLC0A4CBBB，基点）。"""
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
    hy_s = hy_df.set_index("date")["value"]
    ig_s = ig_df.set_index("date")["value"]
    common = hy_s.index.intersection(ig_s.index)
    if len(common) == 0:
        return {}
    latest = common[-1]
    hy_val = float(hy_s[latest])
    ig_val = float(ig_s[latest])
    spread = hy_val - ig_val
    # 历史分位
    all_spread = (hy_s - ig_s).dropna()
    hy_pct = float((all_spread < hy_val).mean() * 100)
    ig_pct = float((all_spread < ig_val).mean() * 100)
    spread_pct = float((all_spread < spread).mean() * 100)
    return {
        "hy": round(hy_val, 2),
        "ig": round(ig_val, 2),
        "spread": round(spread, 2),
        "hy_pct": round(hy_pct, 1),
        "ig_pct": round(ig_pct, 1),
        "spread_pct": round(spread_pct, 1),
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
    """计算净流动性指标。

    净流动性 ≈ 美联储总资产 - TGA - 逆回购
    简化版：用 WALCL 变化率衡量缩表速度。
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
    if len(walcl_df) >= 13:
        prev = float(walcl_df["value"].iloc[-13])
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
    crowding_score: int = 0               # 0 ~ +6
    verdict: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


_SNAPSHOT_CACHE_NAME = "us_crowding_snapshot.json"
_SNAPSHOT_TTL_HOURS = 24


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
        cached_at = raw.get("_cached_at") or raw.get("as_of")
        if not cached_at:
            return None
        ts = datetime.fromisoformat(str(cached_at)[:19])
        age_h = (datetime.now() - ts).total_seconds() / 3600.0
        if age_h > float(max_age_hours):
            return None
        raw.pop("_cached_at", None)
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
    try:
        snap = compute_crowding_snapshot()
        data = snap.to_dict()
        save_crowding_snapshot_cache(data)
        return data
    except Exception as exc:
        logger.warning(f"计算美股拥挤度失败: {exc}")
        # 联网失败时尽量退回过期缓存，避免整页卡住
        stale = load_cached_crowding_snapshot(max_age_hours=24 * 30)
        if stale is not None:
            stale["from_stale_cache"] = True
            return stale
        return None


def compute_crowding_snapshot() -> USCrowdingSnapshot:
    """计算当前美股拥挤度快照（会联网；GUI 读库请用 load_or_compute_crowding_snapshot）。"""
    snap = USCrowdingSnapshot()
    snap.as_of = datetime.now().strftime("%Y-%m-%d")
    score = 0

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
            score += 1

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
        if len(sox_df) >= 60:
            v60 = float(sox_df["close"].iloc[-60])
            snap.sox_60d_ret = float((snap.sox_last / v60 - 1) * 100)
        if len(sox_df) >= 252:
            recent = sox_df.tail(252)
            snap.sox_pct = float(
                (recent["close"] < snap.sox_last).mean() * 100
            )
        if snap.sox_60d_ret is not None and snap.sox_60d_ret > 30:
            snap.sox_overbought = True
            score += 1

    # --- M2 ---
    m2_df = fetch_m2(start="2010-01-01")
    if not m2_df.empty:
        snap.m2_bn = float(m2_df["value"].iloc[-1])  # 十亿美元

    # --- FINRA 保证金债务 ---
    finra_df = fetch_finra_margin_debt()
    if not finra_df.empty:
        snap.margin_debt_bn = float(finra_df["margin_debt_bn"].iloc[-1])

    # --- 保证金债务 / M2 ---
    if snap.margin_debt_bn is not None and snap.m2_bn is not None and snap.m2_bn > 0:
        snap.margin_m2_pct = float(snap.margin_debt_bn / snap.m2_bn * 100)
        if snap.margin_m2_pct >= 6.0:
            snap.margin_m2_danger = True
            score += 1
        elif snap.margin_m2_pct >= 5.0:
            score += 0  # 偏高但未到危险线

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
            score += 1

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
        snap.hy_ig_pct = credit.get("spread_pct")
        if snap.hy_ig_pct is not None and snap.hy_ig_pct >= 80:
            snap.credit_stress = True
            score += 1

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
    snap.crowding_score = score
    if score >= 5:
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

    Returns:
        DataFrame(date, margin_debt_bn, m2_bn, margin_m2_pct)
    """
    if finra_df.empty or m2_df.empty:
        return pd.DataFrame(columns=["date", "margin_debt_bn", "m2_bn", "margin_m2_pct"])

    f = finra_df.copy()
    f["date"] = pd.to_datetime(f["date"]).dt.normalize()
    m = m2_df.copy()
    m["date"] = pd.to_datetime(m["date"]).dt.normalize()
    # M2 是月度，保证金债务也是月度，按月对齐
    merged = pd.merge_asof(
        f.sort_values("date"),
        m.sort_values("date").rename(columns={"value": "m2_bn"}),
        on="date",
        direction="backward",
    )
    merged = merged.dropna(subset=["margin_debt_bn", "m2_bn"])
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
    print(f"综合评分: {d.get('crowding_score', 0)}/6")
    print(f"诊断: {d.get('verdict', '')}")
    print(f"{'='*60}")
