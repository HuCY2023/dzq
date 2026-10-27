# -*- coding: utf-8 -*-
"""大周期 DuckDB：仅保留 2015-01-01 至今。

更新节奏按各指标自然颗粒度（日/周/月），不强制统一月/季。
"""

from __future__ import annotations

import functools
import os
from datetime import date, datetime
from typing import Any, TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection


def _duckdb():
    """GUI 首屏会 import store 常量；勿顶层拉 duckdb（冷启动可达数秒）。"""
    import duckdb

    return duckdb


HISTORY_START = date(2015, 1, 1)
HISTORY_START_STR = "2015-01-01"

# init_schema 幂等但重复跑 DDL 有开销；用标记跳过同连接内的重复调用
_schema_inited: set[int] = set()  # id(con) -> 已初始化

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
    "fund_issue": "day",  # 新成立公募，按成立日；周合计在诊断层
    "holder_reduce": "day",  # 重要股东减持公告，按变动截止日
    "industry_flow_daily": "day",  # 东财行业主力，窗口约 120 日，日更累积
}


def db_path() -> str:
    from output_paths import dp_cycle_store_dir

    root = dp_cycle_store_dir()
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "dp_cycle.duckdb")


def connect(*, read_only: bool = False, retries: int = 8, retry_delay: float = 0.6) -> DuckDBPyConnection:
    """打开 DuckDB；文件被占用时短暂重试（GUI 更新与后台读库常撞车）。"""
    import time

    duckdb = _duckdb()
    path = db_path()
    last_exc: Exception | None = None
    for attempt in range(max(1, int(retries))):
        try:
            if read_only and not os.path.isfile(path):
                # 空库时只读会失败，改写模式建库
                con = duckdb.connect(path)
                init_schema(con)
                return con
            return duckdb.connect(path, read_only=read_only and os.path.isfile(path))
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            msg = str(exc)
            locked = (
                "另一个程序正在使用此文件" in msg
                or "being used by another process" in msg.lower()
                or "Cannot open file" in msg
            )
            if not locked or attempt + 1 >= retries:
                if locked:
                    raise RuntimeError(
                        "大周期库正被占用（可能上次「更新入库」尚未结束）。"
                        "请等状态栏完成，或关掉其它打开该库的进程后重试。"
                        f"\n原始错误: {exc}"
                    ) from exc
                raise
            time.sleep(float(retry_delay) * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def init_schema(con: DuckDBPyConnection) -> None:
    cid = id(con)
    if cid in _schema_inited:
        return
    _schema_inited.add(cid)
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
            pct_5y DOUBLE,
            sh_main_yi DOUBLE,
            sz_main_yi DOUBLE,
            chinext_yi DOUBLE,
            star_yi DOUBLE
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
        CREATE TABLE IF NOT EXISTS fund_issue (
            code VARCHAR PRIMARY KEY,
            name VARCHAR,
            found_date DATE,
            shares_yi DOUBLE,
            fund_type VARCHAR
        );
        CREATE TABLE IF NOT EXISTS holder_reduce (
            code VARCHAR,
            holder VARCHAR,
            end_date DATE,
            notice_date DATE,
            change_wan DOUBLE,
            close_price DOUBLE,
            amount_yi DOUBLE,
            PRIMARY KEY (code, holder, end_date, notice_date)
        );
        CREATE TABLE IF NOT EXISTS industry_flow_daily (
            date DATE,
            bk_code VARCHAR,
            name VARCHAR,
            net_yi DOUBLE,
            mid_yi DOUBLE,
            small_yi DOUBLE,
            PRIMARY KEY (date, bk_code)
        );
        """
    )
    for stmt in (
        "ALTER TABLE main_force_daily ADD COLUMN IF NOT EXISTS small_yi DOUBLE",
        "ALTER TABLE main_force_daily ADD COLUMN IF NOT EXISTS mid_yi DOUBLE",
        "ALTER TABLE main_force_daily ADD COLUMN IF NOT EXISTS large_yi DOUBLE",
        "ALTER TABLE main_force_daily ADD COLUMN IF NOT EXISTS super_yi DOUBLE",
        "ALTER TABLE margin_daily ADD COLUMN IF NOT EXISTS sh_main_yi DOUBLE",
        "ALTER TABLE margin_daily ADD COLUMN IF NOT EXISTS sz_main_yi DOUBLE",
        "ALTER TABLE margin_daily ADD COLUMN IF NOT EXISTS chinext_yi DOUBLE",
        "ALTER TABLE margin_daily ADD COLUMN IF NOT EXISTS star_yi DOUBLE",
    ):
        try:
            con.execute(stmt)
        except Exception:
            pass
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


def _migrate_high_low_source_key(con: DuckDBPyConnection) -> None:
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


def preferred_high_low_source(con: DuckDBPyConnection, start: str) -> str | None:
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
    con: DuckDBPyConnection,
    table: str,
    df: pd.DataFrame,
    *,
    key_cols: list[str],
) -> int:
    if df is None or df.empty:
        return 0
    # init_schema 已在首次调用时跑过，跳过重复 DDL
    init_schema(con)
    clean = df.drop_duplicates(subset=key_cols, keep="last")
    # 避免不必要的 .copy()；只在需要转换日期列时才拷贝
    needs_copy = False
    for c in key_cols:
        if c in clean.columns and str(clean[c].dtype).startswith("datetime"):
            needs_copy = True
            break
    if needs_copy:
        clean = clean.copy()
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
    con: DuckDBPyConnection,
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


def log_run(con: DuckDBPyConnection, *, mode: str, ok: bool, detail: str) -> None:
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
            "fund_issue",
            "holder_reduce",
            "industry_flow_daily",
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


# load_from_db 缓存：同一次进程内，如果库文件未变且参数相同，直接返回上次结果
_load_cache: dict[str, Any] = {"key": None, "val": None}


def _invalidate_load_cache() -> None:
    """入库后调用此函数使缓存失效。"""
    _load_cache["key"] = None
    _load_cache["val"] = None


def load_from_db(
    *,
    index_symbol: str = "sh000001",
    start: str = HISTORY_START_STR,
) -> dict[str, pd.DataFrame]:
    # 缓存检查：库文件 mtime + 参数作为 key
    path = db_path()
    if os.path.isfile(path):
        mtime = os.path.getmtime(path)
        cache_key = f"{path}:{mtime}:{index_symbol}:{start}"
        if _load_cache["key"] == cache_key and _load_cache["val"] is not None:
            return _load_cache["val"]
    else:
        cache_key = None
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
        "fund_issue": pd.DataFrame(),
        "holder_reduce": pd.DataFrame(),
        "industry_flow_daily": pd.DataFrame(),
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

        try:
            mf = con.execute(
                """
                SELECT date, net_yi AS 净额_亿元, inflow_yi AS 流入_亿元,
                       outflow_yi AS 流出_亿元, industry_n AS 行业数,
                       small_yi AS 小单_亿元, mid_yi AS 中单_亿元,
                       large_yi AS 大单_亿元, super_yi AS 超大单_亿元
                FROM main_force_daily WHERE date >= ? ORDER BY date
                """,
                [start_ts],
            ).df()
        except Exception:
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

        try:
            margin = con.execute(
                """
                SELECT date, financing_yi AS 融资余额_亿元,
                       margin_total_yi AS 两融余额_亿元,
                       pct_all AS 分位_全样本, pct_5y AS 分位_近N年,
                       sh_main_yi AS 沪主板_亿元,
                       sz_main_yi AS 深主板_亿元,
                       chinext_yi AS 创业板_亿元,
                       star_yi AS 科创板_亿元,
                       (COALESCE(sh_main_yi, 0) + COALESCE(sz_main_yi, 0)) AS 主板_亿元,
                       (COALESCE(sz_main_yi, 0) + COALESCE(chinext_yi, 0)) AS 深市_亿元
                FROM margin_daily WHERE date >= ? ORDER BY date
                """,
                [start_ts],
            ).df()
            # 无分板日：主板/深市置空，避免 COALESCE 把缺数写成 0
            if not margin.empty and "沪主板_亿元" in margin.columns:
                miss = margin["沪主板_亿元"].isna()
                for col in ("主板_亿元", "深市_亿元"):
                    if col in margin.columns:
                        margin.loc[miss, col] = pd.NA
        except Exception:
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

        def _safe(sql: str) -> pd.DataFrame:
            try:
                return con.execute(sql, [start_ts]).df()
            except Exception:
                return pd.DataFrame()

        fund_issue = _safe(
            """
            SELECT code, name, found_date AS date, shares_yi, fund_type
            FROM fund_issue WHERE found_date >= ? ORDER BY found_date
            """
        )
        holder_reduce = _safe(
            """
            SELECT code, holder, end_date AS date, notice_date, change_wan, close_price, amount_yi
            FROM holder_reduce WHERE end_date >= ? ORDER BY end_date
            """
        )
        industry_flow = _safe(
            """
            SELECT date, bk_code, name, net_yi, mid_yi, small_yi
            FROM industry_flow_daily WHERE date >= ? ORDER BY date, bk_code
            """
        )

        result = {
            "weekly": weekly,
            "main_force_daily": mf,
            "northbound_daily": nb,
            "etf_daily": etf_daily,
            "etf_hist": etf_hist,
            "margin": margin,
            "growth_value": gv,
            "high_low": hl,
            "unlock_daily": unlock,
            "fund_issue": fund_issue,
            "holder_reduce": holder_reduce,
            "industry_flow_daily": industry_flow,
        }
        # 存入缓存
        if cache_key is not None:
            _load_cache["key"] = cache_key
            _load_cache["val"] = result
        return result
    finally:
        con.close()
