# -*- coding: utf-8 -*-
"""大周期 DuckDB：仅保留 2015-01-01 至今。

更新节奏按各指标自然颗粒度（日/周/月），不强制统一月/季。
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import duckdb
import pandas as pd

HISTORY_START = date(2015, 1, 1)
HISTORY_START_STR = "2015-01-01"

# 建议刷新粒度（说明用）：按源数据颗粒度，日/周/月均可，不强制统一月或季。
# 实际以库内最新日期 + 源能取到的频率为准；慢变序列周更也完全合理。
SERIES_GRAIN = {
    "index_weekly": "week",
    "main_force_daily": "day",  # 东财大盘主力；可日更或周更
    "northbound_daily": "day",  # 北向辅源，2014+ 长历史
    "etf_shares_daily": "day",  # 日频点；周更足够跟申赎
    "margin_daily": "day",  # 日余额；展示层再做月变化
    "growth_value_daily": "day",
    "high_low_daily": "day",  # 乐咕120日快径；湖内52周可覆盖
    "unlock_daily": "day",  # 解禁日统计；网页按月/季加总
}


def db_path() -> str:
    from output_paths import dp_cycle_store_dir

    root = dp_cycle_store_dir()
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "dp_cycle.duckdb")


def connect(*, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = db_path()
    if read_only and not os.path.isfile(path):
        # 空库时只读会失败，改写模式建库
        con = duckdb.connect(path)
        init_schema(con)
        return con
    return duckdb.connect(path, read_only=read_only and os.path.isfile(path))


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_runs (
            ran_at TIMESTAMP,
            mode VARCHAR,
            ok BOOLEAN,
            detail VARCHAR
        );
        CREATE TABLE IF NOT EXISTS series_meta (
            series VARCHAR PRIMARY KEY,
            grain VARCHAR,
            last_date DATE,
            updated_at TIMESTAMP,
            note VARCHAR
        );
        CREATE TABLE IF NOT EXISTS index_weekly (
            symbol VARCHAR,
            date DATE,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            PRIMARY KEY (symbol, date)
        );
        CREATE TABLE IF NOT EXISTS main_force_daily (
            date DATE PRIMARY KEY,
            net_yi DOUBLE,
            inflow_yi DOUBLE,
            outflow_yi DOUBLE,
            industry_n INTEGER,
            source VARCHAR
        );
        CREATE TABLE IF NOT EXISTS etf_shares_daily (
            date DATE,
            code VARCHAR,
            name VARCHAR,
            shares DOUBLE,
            shares_yi DOUBLE,
            PRIMARY KEY (date, code)
        );
        CREATE TABLE IF NOT EXISTS margin_daily (
            date DATE PRIMARY KEY,
            financing_yi DOUBLE,
            margin_total_yi DOUBLE,
            pct_all DOUBLE,
            pct_5y DOUBLE
        );
        CREATE TABLE IF NOT EXISTS growth_value_daily (
            date DATE PRIMARY KEY,
            pe_ratio DOUBLE,
            pb_ratio DOUBLE,
            divergence DOUBLE,
            g_pe DOUBLE,
            v_pe DOUBLE,
            g_pb DOUBLE,
            v_pb DOUBLE
        );
        CREATE TABLE IF NOT EXISTS high_low_daily (
            date DATE,
            new_high INTEGER,
            new_low INTEGER,
            diff INTEGER,
            universe INTEGER,
            index_close DOUBLE,
            source VARCHAR,
            PRIMARY KEY (date, source)
        );
        CREATE TABLE IF NOT EXISTS northbound_daily (
            date DATE PRIMARY KEY,
            net_yi DOUBLE,
            source VARCHAR
        );
        CREATE TABLE IF NOT EXISTS unlock_daily (
            date DATE,
            universe VARCHAR,
            amount_yi DOUBLE,
            plan_yi DOUBLE,
            stock_n INTEGER,
            source VARCHAR,
            PRIMARY KEY (date, universe)
        );
        """
    )
    # 兼容旧库缺列；乐咕 120 日与湖 252 日必须分键共存，禁止按 date 互相覆盖
    try:
        con.execute("ALTER TABLE high_low_daily ADD COLUMN IF NOT EXISTS source VARCHAR")
    except Exception:
        pass
    _migrate_high_low_source_key(con)
    for series, grain in SERIES_GRAIN.items():
        exists = con.execute(
            "SELECT 1 FROM series_meta WHERE series = ? LIMIT 1", [series]
        ).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO series_meta VALUES (?, ?, NULL, NULL, ?)",
                [series, grain, "建议按颗粒度刷新（日/周/月均可）"],
            )


def _migrate_high_low_source_key(con: duckdb.DuckDBPyConnection) -> None:
    """旧表主键只有 date 时，乐咕尾部会接到湖扫序列上。改成 (date, source)。"""
    info = con.execute("PRAGMA table_info('high_low_daily')").fetchall()
    pk_names = [r[1] for r in info if len(r) > 5 and r[5]]
    if "date" in pk_names and "source" in pk_names:
        return
    con.execute("DROP TABLE IF EXISTS high_low_daily__pk")
    con.execute(
        """
        CREATE TABLE high_low_daily__pk (
            date DATE,
            new_high INTEGER,
            new_low INTEGER,
            diff INTEGER,
            universe INTEGER,
            index_close DOUBLE,
            source VARCHAR,
            PRIMARY KEY (date, source)
        )
        """
    )
    con.execute(
        """
        INSERT INTO high_low_daily__pk
        SELECT date, new_high, new_low, diff, universe, index_close, source
        FROM (
            SELECT
                date, new_high, new_low, diff, universe, index_close,
                COALESCE(NULLIF(CAST(source AS VARCHAR), ''), 'unknown') AS source,
                ROW_NUMBER() OVER (
                    PARTITION BY date, COALESCE(NULLIF(CAST(source AS VARCHAR), ''), 'unknown')
                    ORDER BY date
                ) AS rn
            FROM high_low_daily
        ) t
        WHERE rn = 1
        """
    )
    con.execute("DROP TABLE high_low_daily")
    con.execute("ALTER TABLE high_low_daily__pk RENAME TO high_low_daily")


def preferred_high_low_source(con: duckdb.DuckDBPyConnection, start: str) -> str | None:
    """展示只用一种口径。湖扫够长时用 252 日，否则退回乐咕 120 日，禁止拼接。"""
    rows = con.execute(
        """
        SELECT COALESCE(NULLIF(CAST(source AS VARCHAR), ''), 'unknown') AS source,
               COUNT(*) AS n
        FROM high_low_daily
        WHERE date >= ?
        GROUP BY 1
        ORDER BY n DESC
        """,
        [str(start)[:10]],
    ).fetchall()
    if not rows:
        return None
    counts = {str(r[0]): int(r[1]) for r in rows}
    if counts.get("lake_52w", 0) >= 200:
        return "lake_52w"
    if counts.get("legu_120d", 0) >= 30:
        return "legu_120d"
    return str(rows[0][0])


def clip_from_2015(df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.iloc[0:0].copy()
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
    out = out.dropna(subset=[date_col])
    out = out.loc[out[date_col] >= pd.Timestamp(HISTORY_START)].copy()
    out[date_col] = out[date_col].dt.normalize()
    return out.sort_values(date_col).reset_index(drop=True)


def upsert_dataframe(
    con: duckdb.DuckDBPyConnection,
    table: str,
    df: pd.DataFrame,
    *,
    key_cols: list[str],
) -> int:
    if df is None or df.empty:
        return 0
    init_schema(con)
    clean = df.drop_duplicates(subset=key_cols, keep="last").copy()
    for c in key_cols:
        if c in clean.columns and str(clean[c].dtype).startswith("datetime"):
            clean[c] = pd.to_datetime(clean[c]).dt.date
    tmp = f"_tmp_{table}"
    con.register(tmp, clean)
    cols = list(clean.columns)
    col_list = ", ".join(cols)
    # PRIMARY KEY 表用 OR REPLACE，避免 DELETE USING 与注册视图类型不一致漏删
    con.execute(f"INSERT OR REPLACE INTO {table} ({col_list}) SELECT {col_list} FROM {tmp}")
    con.unregister(tmp)
    return int(len(clean))


def touch_series(
    con: duckdb.DuckDBPyConnection,
    series: str,
    *,
    last_date: date | datetime | pd.Timestamp | None,
    note: str = "",
) -> None:
    init_schema(con)
    grain = SERIES_GRAIN.get(series, "day")
    ld = None
    if last_date is not None and not pd.isna(last_date):
        ld = pd.Timestamp(last_date).date()
    con.execute("DELETE FROM series_meta WHERE series = ?", [series])
    con.execute(
        "INSERT INTO series_meta VALUES (?, ?, ?, ?, ?)",
        [series, grain, ld, datetime.now(), note or f"grain={grain}"],
    )


def log_run(con: duckdb.DuckDBPyConnection, *, mode: str, ok: bool, detail: str) -> None:
    init_schema(con)
    con.execute(
        "INSERT INTO meta_runs VALUES (?, ?, ?, ?)",
        [datetime.now(), mode, ok, (detail or "")[:4000]],
    )


def latest_run() -> dict[str, Any] | None:
    if not os.path.isfile(db_path()):
        return None
    con = connect(read_only=True)
    try:
        row = con.execute(
            "SELECT ran_at, mode, ok, detail FROM meta_runs ORDER BY ran_at DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return None
    finally:
        con.close()
    if not row:
        return None
    return {"ran_at": row[0], "mode": row[1], "ok": row[2], "detail": row[3]}


def series_status() -> pd.DataFrame:
    if not os.path.isfile(db_path()):
        return pd.DataFrame(columns=["series", "grain", "last_date", "updated_at", "note"])
    con = connect(read_only=True)
    try:
        return con.execute(
            "SELECT series, grain, last_date, updated_at, note FROM series_meta ORDER BY series"
        ).df()
    except Exception:
        return pd.DataFrame(columns=["series", "grain", "last_date", "updated_at", "note"])
    finally:
        con.close()


def table_counts() -> dict[str, int]:
    """空库时写模式建表；已有库也可只读统计。"""
    path = db_path()
    if not os.path.isfile(path):
        con = connect()
        try:
            init_schema(con)
        finally:
            con.close()
    con = connect(read_only=True)
    try:
        out: dict[str, int] = {}
        for t in (
            "index_weekly",
            "main_force_daily",
            "northbound_daily",
            "etf_shares_daily",
            "margin_daily",
            "growth_value_daily",
            "high_low_daily",
            "unlock_daily",
        ):
            try:
                out[t] = int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            except Exception:
                out[t] = 0
        return out
    finally:
        con.close()


def list_weekly_symbols() -> list[str]:
    """库里已有周线的指数代码。"""
    if not os.path.isfile(db_path()):
        return []
    con = connect(read_only=True)
    try:
        rows = con.execute(
            "SELECT symbol, COUNT(*) AS n FROM index_weekly GROUP BY symbol ORDER BY n DESC"
        ).fetchall()
        return [str(r[0]) for r in rows if r and r[0]]
    except Exception:
        return []
    finally:
        con.close()


def load_from_db(
    *,
    index_symbol: str = "sh000001",
    start: str = HISTORY_START_STR,
) -> dict[str, pd.DataFrame]:
    empty = {
        "weekly": pd.DataFrame(),
        "main_force_daily": pd.DataFrame(),
        "northbound_daily": pd.DataFrame(),
        "etf_daily": pd.DataFrame(),
        "etf_hist": pd.DataFrame(),
        "margin": pd.DataFrame(),
        "growth_value": pd.DataFrame(),
        "high_low": pd.DataFrame(),
        "unlock_daily": pd.DataFrame(),
    }
    if not os.path.isfile(db_path()):
        return empty
    con = connect(read_only=True)
    try:
        start_ts = str(start)[:10]

        weekly = con.execute(
            """
            SELECT date, open, high, low, close, volume
            FROM index_weekly
            WHERE symbol = ? AND date >= ?
            ORDER BY date
            """,
            [index_symbol, start_ts],
        ).df()

        mf = con.execute(
            """
            SELECT date, net_yi AS 净额_亿元, inflow_yi AS 流入_亿元,
                   outflow_yi AS 流出_亿元, industry_n AS 行业数
            FROM main_force_daily WHERE date >= ? ORDER BY date
            """,
            [start_ts],
        ).df()

        try:
            nb = con.execute(
                """
                SELECT date, net_yi AS 净额_亿元
                FROM northbound_daily WHERE date >= ? ORDER BY date
                """,
                [start_ts],
            ).df()
        except Exception:
            nb = pd.DataFrame()

        etf_hist = con.execute(
            """
            SELECT date, code, name, shares AS 基金份额, shares_yi AS 份额_亿份
            FROM etf_shares_daily WHERE date >= ? ORDER BY date, code
            """,
            [start_ts],
        ).df()
        if etf_hist.empty:
            etf_daily = pd.DataFrame(columns=["date", "份额_亿份"])
        else:
            etf_daily = (
                etf_hist.groupby("date", as_index=False)["份额_亿份"].sum().sort_values("date")
            )

        margin = con.execute(
            """
            SELECT date, financing_yi AS 融资余额_亿元,
                   margin_total_yi AS 两融余额_亿元,
                   pct_all AS 分位_全样本, pct_5y AS 分位_近N年
            FROM margin_daily WHERE date >= ? ORDER BY date
            """,
            [start_ts],
        ).df()

        gv = con.execute(
            """
            SELECT date, pe_ratio AS PE比, pb_ratio AS PB比,
                   divergence AS 偏离度, g_pe, v_pe, g_pb, v_pb
            FROM growth_value_daily WHERE date >= ? ORDER BY date
            """,
            [start_ts],
        ).df()
        if not gv.empty:
            gv["作者阈值_17"] = 17.0

        hl_source = preferred_high_low_source(con, start_ts)
        if hl_source:
            hl = con.execute(
                """
                SELECT date, new_high, new_low, diff AS "新高-新低",
                       universe, index_close, source
                FROM high_low_daily
                WHERE date >= ? AND COALESCE(NULLIF(CAST(source AS VARCHAR), ''), 'unknown') = ?
                ORDER BY date
                """,
                [start_ts, hl_source],
            ).df()
        else:
            hl = pd.DataFrame()

        try:
            unlock = con.execute(
                """
                SELECT date, universe, amount_yi, plan_yi, stock_n, source
                FROM unlock_daily WHERE date >= ? ORDER BY date, universe
                """,
                [start_ts],
            ).df()
        except Exception:
            unlock = pd.DataFrame()

        return {
            "weekly": weekly,
            "main_force_daily": mf,
            "northbound_daily": nb,
            "etf_daily": etf_daily,
            "etf_hist": etf_hist,
            "margin": margin,
            "growth_value": gv,
            "high_low": hl,
            "unlock_daily": unlock,
        }
    finally:
        con.close()
