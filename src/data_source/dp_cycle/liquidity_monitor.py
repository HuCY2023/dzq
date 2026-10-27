# -*- coding: utf-8 -*-
"""大周期流动性节点监控 — 国内外流动性方向跟踪。

数据来源（全部免费、无需 API Key）：
- 上证指数周K：akshare stock_zh_index_daily(symbol="sh000001")
- 恒生科技指数周K：akshare stock_hk_index_daily_em(symbol="HSTECH")
- 黄金期货周K：akshare futures_main_sina(symbol="AU0")
- 10年期美债收益率：FRED CSV (DGS10)
- 30年期美债收益率：FRED CSV (DGS30)
- 美联储议息时间点：硬编码 FOMC 会议日期

缓存策略：DuckDB 本地持久化，周数据 7 天刷新，日数据 1 天刷新。
监控区间：2016 至今 → 未来半年
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────

DATA_START = "2016-01-01"
# 缓存有效期（天）
_CACHE_TTL_WEEKLY = 7   # 周K数据
_CACHE_TTL_DAILY = 1    # 日频数据


def _db_path() -> Path:
    """流动性监控缓存 DB 路径。"""
    from output_paths import dp_cycle_store_dir
    db_dir = Path(dp_cycle_store_dir())
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "liquidity_monitor.duckdb"
FOMC_DATES: list[str] = [
    # 2016
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15",
    "2016-07-27", "2016-09-21", "2016-11-02", "2016-12-14",
    # 2017
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14",
    "2017-07-26", "2017-09-20", "2017-11-01", "2017-12-13",
    # 2018
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13",
    "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    # 2019
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19",
    "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    # 2020
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29",
    "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    # 2021
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16",
    "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    # 2022
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    # 2023
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026 (预估，实际以 Fed 公布为准)
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
]


# ──────────────────────────────────────────────
# FRED 数据获取
# ──────────────────────────────────────────────

_FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}&coed={end}"


def _fetch_fred(series_id: str, start: str = DATA_START) -> pd.DataFrame:
    """从 FRED 公开 CSV 端点拉取数据，返回 DataFrame(date, value)。"""
    end = datetime.now().strftime("%Y-%m-%d")
    url = _FRED_CSV_URL.format(series_id=series_id, start=start, end=end)
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text), usecols=["observation_date", series_id])
        df = df.rename(columns={"observation_date": "date", series_id: "value"})
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["date", "value"]).sort_values("date").reset_index(drop=True)
        return df
    except Exception as exc:
        logger.warning(f"FRED {series_id} 获取失败: {exc}")
        return pd.DataFrame(columns=["date", "value"])


# ──────────────────────────────────────────────
# 日K → 周K 转换
# ──────────────────────────────────────────────

def _daily_to_weekly(df: pd.DataFrame, *, date_col: str = "date",
                     ohlc: tuple[str, ...] = ("open", "high", "low", "close")) -> pd.DataFrame:
    """将日 K 线聚合为周 K 线。"""
    if df.empty:
        return df
    tmp = df.copy()
    tmp[date_col] = pd.to_datetime(tmp[date_col])
    tmp = tmp.set_index(date_col).sort_index()
    agg = {c: "first" if c == ohlc[0] else ("max" if c == ohlc[1] else ("min" if c == ohlc[2] else "last"))
           for c in ohlc}
    if "volume" in tmp.columns:
        agg["volume"] = "sum"
    weekly = tmp.resample("W-FRI").agg(agg).dropna(subset=[ohlc[0]])
    weekly = weekly.reset_index()
    return weekly


# ──────────────────────────────────────────────
# 各数据源获取函数
# ──────────────────────────────────────────────

def fetch_shanghai_index() -> pd.DataFrame:
    """上证指数日K → 周K。"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000001")
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns={"date": "date"})
        df["date"] = pd.to_datetime(df["date"])
        # Filter from 2016
        df = df[df["date"] >= DATA_START].copy()
        weekly = _daily_to_weekly(df)
        return weekly
    except Exception as exc:
        logger.warning(f"上证指数获取失败: {exc}")
        return pd.DataFrame()


def fetch_hstech_index() -> pd.DataFrame:
    """恒生科技指数日K → 周K。"""
    import time
    for attempt in range(3):
        try:
            import akshare as ak
            df = ak.stock_hk_index_daily_em(symbol="HSTECH")
            if df.empty:
                return pd.DataFrame()
            df = df.rename(columns={"latest": "close"})
            df["date"] = pd.to_datetime(df["date"])
            df = df[df["date"] >= DATA_START].copy()
            # 东财港股指数只有 OHLC (open, high, low, close)
            weekly = _daily_to_weekly(df, ohlc=("open", "high", "low", "close"))
            return weekly
        except Exception as exc:
            logger.warning(f"恒生科技指数获取失败 (尝试 {attempt+1}/3): {exc}")
            if attempt < 2:
                time.sleep(3)
    return pd.DataFrame()


def fetch_gold_futures() -> pd.DataFrame:
    """黄金期货（AU0 主力合约）日K → 周K。"""
    try:
        import akshare as ak
        df = ak.futures_main_sina(symbol="AU0", start_date="20160101", end_date="20261231")
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            "日期": "date", "开盘价": "open", "最高价": "high",
            "最低价": "low", "收盘价": "close", "成交量": "volume",
        })
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["date"] >= DATA_START].copy()
        weekly = _daily_to_weekly(df)
        return weekly
    except Exception as exc:
        logger.warning(f"黄金期货获取失败: {exc}")
        return pd.DataFrame()


def fetch_us_treasury_10y() -> pd.DataFrame:
    """10年期美债收益率（日频，FRED DGS10）。"""
    return _fetch_fred("DGS10")


def fetch_us_treasury_30y() -> pd.DataFrame:
    """30年期美债收益率（日频，FRED DGS30）。"""
    return _fetch_fred("DGS30")


def get_fomc_dates() -> list[str]:
    """返回美联储议息日期列表。"""
    return FOMC_DATES.copy()


# ──────────────────────────────────────────────
# DuckDB 缓存
# ──────────────────────────────────────────────

def _load_cache() -> dict[str, Any] | None:
    """从 DuckDB 读取缓存，过期返回 None。"""
    db = _db_path()
    if not db.is_file():
        return None
    try:
        import duckdb
        with duckdb.connect(str(db), read_only=True) as con:
            # 检查元数据
            meta = con.execute("SELECT key, value FROM cache_meta WHERE key='updated_at'").fetchall()
            if not meta:
                return None
            updated_str = meta[0][1]
            updated = datetime.fromisoformat(updated_str)
            age_days = (datetime.now() - updated).total_seconds() / 86400
            # 主图多为周K：默认 7 天内直接读库；需要最新时点「刷新流动性数据」
            if age_days > _CACHE_TTL_WEEKLY:
                logger.info(f"流动性缓存已过期 ({age_days:.1f} 天)，重新获取")
                return None

            # 读取各表（表可能不存在，跳过）
            result: dict[str, Any] = {}
            tables_exist = con.execute("SHOW TABLES").fetchall()
            table_names = {row[0] for row in tables_exist}

            # 检查完整性：至少要有 shanghai + gold + treasury_10y + treasury_30y
            # hstech 允许缺失（网络不稳定时不影响其他指标展示）
            required_keys = {"shanghai", "gold", "treasury_10y", "treasury_30y"}
            if not required_keys.issubset(table_names):
                missing = required_keys - table_names
                logger.info(f"流动性缓存缺少必需表: {missing}，重新获取")
                return None

            for table in ("shanghai", "hstech", "gold"):
                if table not in table_names:
                    continue
                df = con.execute(f"SELECT * FROM {table}").fetchdf()
                if not df.empty:
                    result[table] = {
                        "dates": df["date"].dt.strftime("%Y-%m-%d").tolist(),
                        "open": df["open"].round(2).tolist(),
                        "high": df["high"].round(2).tolist(),
                        "low": df["low"].round(2).tolist(),
                        "close": df["close"].round(2).tolist(),
                    }
            for table in ("treasury_10y", "treasury_30y"):
                if table not in table_names:
                    continue
                df = con.execute(f"SELECT * FROM {table}").fetchdf()
                if not df.empty:
                    result[table] = {
                        "dates": df["date"].dt.strftime("%Y-%m-%d").tolist(),
                        "values": df["value"].round(3).tolist(),
                    }
            result["fomc_dates"] = get_fomc_dates()
            result["meta"] = {
                "generated_at": updated_str,
                "data_start": DATA_START,
                "data_end": updated.strftime("%Y-%m-%d"),
                "view_end": (datetime.now() + timedelta(days=180)).strftime("%Y-%m-%d"),
                "from_cache": True,
            }
            logger.info(f"流动性缓存命中 ({age_days:.1f} 天)")
            return result
    except Exception as exc:
        logger.warning(f"读取流动性缓存失败: {exc}")
        return None


def _save_cache(data: dict[str, Any]) -> None:
    """将流动性数据写入 DuckDB 缓存。"""
    db = _db_path()
    try:
        import duckdb
        with duckdb.connect(str(db)) as con:
            # 创建/替换表
            for table in ("shanghai", "hstech", "gold"):
                if table in data:
                    d = data[table]
                    df = pd.DataFrame({
                        "date": pd.to_datetime(d["dates"]),
                        "open": d["open"],
                        "high": d["high"],
                        "low": d["low"],
                        "close": d["close"],
                    })
                    con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM df")
            for table in ("treasury_10y", "treasury_30y"):
                if table in data:
                    d = data[table]
                    df = pd.DataFrame({
                        "date": pd.to_datetime(d["dates"]),
                        "value": d["values"],
                    })
                    con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM df")
            # 元数据
            con.execute("""
                CREATE OR REPLACE TABLE cache_meta (key VARCHAR, value VARCHAR)
            """)
            con.execute("""
                INSERT INTO cache_meta VALUES
                ('updated_at', ?),
                ('data_start', ?)
            """, [datetime.now().isoformat(), DATA_START])
            logger.info(f"流动性缓存已写入: {db}")
    except Exception as exc:
        logger.warning(f"写入流动性缓存失败: {exc}")


# ──────────────────────────────────────────────
# 汇总
# ──────────────────────────────────────────────

def fetch_all_liquidity_data(*, force_refresh: bool = False) -> dict[str, Any]:
    """一次性获取所有流动性监控数据，返回供 Web 图表渲染的 payload。
    
    Args:
        force_refresh: 强制重新获取，忽略缓存。
    """
    # 优先读缓存
    if not force_refresh:
        cached = _load_cache()
        if cached:
            return cached

    # 缓存未命中或强制刷新，从网络获取
    result: dict[str, Any] = {}

    # 上证指数
    sh = fetch_shanghai_index()
    if not sh.empty:
        result["shanghai"] = {
            "dates": [d.strftime("%Y-%m-%d") for d in sh["date"]],
            "open": sh["open"].round(2).tolist(),
            "high": sh["high"].round(2).tolist(),
            "low": sh["low"].round(2).tolist(),
            "close": sh["close"].round(2).tolist(),
        }

    # 恒生科技
    hs = fetch_hstech_index()
    if not hs.empty:
        result["hstech"] = {
            "dates": [d.strftime("%Y-%m-%d") for d in hs["date"]],
            "open": hs["open"].round(2).tolist(),
            "high": hs["high"].round(2).tolist(),
            "low": hs["low"].round(2).tolist(),
            "close": hs["close"].round(2).tolist(),
        }

    # 黄金期货
    au = fetch_gold_futures()
    if not au.empty:
        result["gold"] = {
            "dates": [d.strftime("%Y-%m-%d") for d in au["date"]],
            "open": au["open"].round(2).tolist(),
            "high": au["high"].round(2).tolist(),
            "low": au["low"].round(2).tolist(),
            "close": au["close"].round(2).tolist(),
        }

    # 10Y 美债
    t10 = fetch_us_treasury_10y()
    if not t10.empty:
        result["treasury_10y"] = {
            "dates": [d.strftime("%Y-%m-%d") for d in t10["date"]],
            "values": t10["value"].round(3).tolist(),
        }

    # 30Y 美债
    t30 = fetch_us_treasury_30y()
    if not t30.empty:
        result["treasury_30y"] = {
            "dates": [d.strftime("%Y-%m-%d") for d in t30["date"]],
            "values": t30["value"].round(3).tolist(),
        }

    # FOMC 日期
    result["fomc_dates"] = get_fomc_dates()

    # 元数据
    result["meta"] = {
        "generated_at": datetime.now().isoformat(),
        "data_start": DATA_START,
        "data_end": datetime.now().strftime("%Y-%m-%d"),
        "view_end": (datetime.now() + timedelta(days=180)).strftime("%Y-%m-%d"),
        "from_cache": False,
    }

    # 写入缓存
    _save_cache(result)

    return result
