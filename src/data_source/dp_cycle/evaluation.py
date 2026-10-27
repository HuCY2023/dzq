"""Small, evidence-gated allocation replay; observations are not recommendations."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

import numpy as np
import pandas as pd


DEFAULT_ONE_WAY_COST = 0.0005
_OMISSIONS = [
    "趋势、波动率及相近股票暴露基准尚未实现。",
    "递进分区、隔离期、预警标签和单项消融尚未实现，不能视为完整有效性验证。",
    "来源和复权凭证由调用方提供，程序不独立认证其真实性。",
    "可成交状态由输入提供，按连续份额计算，未单独模拟整手、涨跌停、滑点及成交容量。",
]


def _local_timestamp(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.notna(stamp) and stamp.tzinfo is not None:
        stamp = stamp.tz_convert("Asia/Shanghai").tz_localize(None)
    return stamp


def _time(value: Any) -> pd.Timestamp:
    if isinstance(value, str) and len(value.strip()) <= 10:
        return pd.NaT  # A date alone does not establish the time of day.
    if isinstance(value, date) and not isinstance(value, datetime):
        return pd.NaT
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        stamp = _local_timestamp(value)
        if pd.notna(stamp) and stamp == stamp.normalize():
            return pd.NaT  # A pandas date promoted to midnight has no time precision.
    try:
        return _local_timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return pd.NaT


def _block(*reasons: str) -> dict[str, Any]:
    return {"status": "BLOCK", "reasons": list(reasons), "limitations": list(_OMISSIONS)}


def _evaluate_strategy(
    prices: pd.DataFrame, signals: pd.DataFrame, *, policy: str, cost: float,
) -> dict[str, Any]:
    cash, units, signal_index = 1.0, 0.0, 0
    pending: dict[str, Any] | None = None
    started = False
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    cancelled_signals: list[dict[str, str]] = []
    unknown_signals: list[dict[str, str]] = []
    total_turnover = 0.0
    total_cost = 0.0
    for bar in prices.itertuples(index=False):
        session = pd.Timestamp(bar.date)
        opening = session + pd.Timedelta(hours=9, minutes=30)
        while policy == "rule" and signal_index < len(signals) and signals.iloc[signal_index]["signal_at"] < opening:
            latest = signals.iloc[signal_index].to_dict()
            if pd.isna(latest["target_equity"]):
                unknown_signals.append({"signal_at": str(latest["signal_at"])})
            if pd.isna(latest["target_equity"]) and pending is not None:
                cancelled_signals.append({"signal_at": str(pending["signal_at"]),
                                          "cancelled_at": str(latest["signal_at"])})
            pending = None if pd.isna(latest["target_equity"]) else latest
            signal_index += 1
        adjusted = float(bar.total_return_open)
        asset = units * adjusted
        before = cash + asset
        target: float | None = None
        if bar.tradable:
            if policy == "rule" and pending is not None:
                target = float(pending["target_equity"])
            elif policy == "buy_hold" and not started:
                target = 1.0
            elif policy == "fixed_50":
                target = 0.5
        traded, charge = 0.0, 0.0
        if target is not None:
            desired_difference = target * before - asset
            if abs(desired_difference) < 1e-12 * before:
                desired_difference = 0.0
            if desired_difference > 0:
                traded = desired_difference / (1.0 + target * cost)
                charge = traded * cost
                cash -= traded + charge
                units += traded / adjusted
                side = "buy"
            elif desired_difference < 0:
                traded = -desired_difference / (1.0 - target * cost)
                charge = traded * cost
                cash += traded - charge
                units -= traded / adjusted
                side = "sell"
            else:
                side = "hold"
            if traded:
                total_turnover += traded / before
                total_cost += charge
                trade = {"date": session.strftime("%Y-%m-%d"), "execution_at": str(opening),
                         "actual_open": float(bar.actual_open), "target_equity": target,
                         "side": side, "trade_value": traded, "cost": charge}
                if policy == "rule":
                    trade["signal_at"] = str(pending["signal_at"])
                    trade["available_at"] = str(pending["available_at"])
                trades.append(trade)
            started = True
            if policy == "rule":
                pending = None
        nav = cash + units * adjusted
        rows.append({"date": session.strftime("%Y-%m-%d"), "nav": float(nav),
                     "equity_fraction": float(units * adjusted / nav), "cost": float(charge)})
    navs = np.array([1.0] + [row["nav"] for row in rows], dtype=float)
    returns = navs[1:] / navs[:-1] - 1.0
    drawdown = navs / np.maximum.accumulate(navs) - 1.0
    metrics = {"net_return": float(navs[-1] - 1.0), "max_drawdown": float(drawdown.min()),
               "annual_volatility": float(np.std(returns, ddof=1) * np.sqrt(252)) if len(returns) > 1 else None,
               "turnover": float(total_turnover),
               "average_equity": float(np.mean([row["equity_fraction"] for row in rows])),
               "total_cost": float(total_cost)}
    unfilled = ({"signal_at": str(pending["signal_at"]), "available_at": str(pending["available_at"]),
                 "target_equity": float(pending["target_equity"])} if pending is not None else None)
    return {"daily": rows, "trades": trades, "metrics": metrics,
            "unfilled_signal": unfilled, "cancelled_signals": cancelled_signals,
            "unknown_signals": unknown_signals}


def evaluate_allocation(
    prices: pd.DataFrame, signals: pd.DataFrame, evidence: dict[str, Any], *,
    trading_dates: Iterable[Any] | None, one_way_cost: float = DEFAULT_ONE_WAY_COST,
) -> dict[str, Any]:
    """Replay signals at the next tradable 09:30 open using total-return prices.

    `actual_open` verifies that an executable price exists; `total_return_open`
    supplies comparable returns after split/dividend adjustment. The provenance
    and point-in-time evidence are assertions supplied by the caller. Only
    inputs with documented evidence can be explored, and the output explicitly
    records the unfinished validation protocol. A missing target is an unknown
    decision: it cancels any pending instruction and retains the current holdings.
    """
    required = ("symbol", "price_source", "calendar_source", "price_adjustment_evidence",
                "dividend_treatment", "signal_vintage_evidence")
    missing = [name for name in required if not evidence.get(name)]
    verified = ("calendar_verified", "point_in_time_verified", "execution_prices_verified", "total_return_verified")
    if (missing or evidence.get("instrument_type") not in {"ETF", "stock"}
            or evidence.get("market") != "CN_A_SHARE"
            or any(evidence.get(name) is not True for name in verified)):
        return _block("缺少可交易标的、交易日历、复权/分红或历史信号可得版本的证明")
    if not 0 <= one_way_cost < 1:
        return _block("交易成本不在有效范围")
    need_prices = {"date", "actual_open", "total_return_open", "tradable"}
    need_signals = {"signal_at", "available_at", "target_equity"}
    if not isinstance(prices, pd.DataFrame) or prices.empty or not need_prices.issubset(prices.columns):
        return _block("缺少可交易价格或总收益复权价格")
    if not isinstance(signals, pd.DataFrame) or not need_signals.issubset(signals.columns):
        return _block("缺少信号生成时间、可得时间或目标仓位")
    p = prices.copy()
    p["date"] = pd.to_datetime(p["date"], errors="coerce").dt.normalize()
    if p["date"].isna().any() or p["date"].duplicated().any():
        return _block("价格交易日缺失或重复")
    p = p.sort_values("date").reset_index(drop=True)
    calendar = pd.to_datetime(list(trading_dates) if trading_dates is not None else [], errors="coerce")
    calendar = pd.DatetimeIndex(calendar).normalize().sort_values()
    if calendar.isna().any() or calendar.has_duplicates or list(calendar) != list(p["date"]):
        return _block("交易日历与价格序列不一致")
    tradable = p["tradable"].map({True: True, False: False, 1: True, 0: False, "true": True, "false": False, "True": True, "False": False})
    if tradable.isna().any():
        return _block("缺少可成交状态")
    p["tradable"] = tradable.astype(bool)
    for col in ("actual_open", "total_return_open"):
        p[col] = pd.to_numeric(p[col], errors="coerce")
    if (p["total_return_open"].isna() | ~np.isfinite(p["total_return_open"]) | p["total_return_open"].le(0)).any() or (
        p["tradable"] & (p["actual_open"].isna() | ~np.isfinite(p["actual_open"]) | p["actual_open"].le(0))
    ).any():
        return _block("价格缺失，不能用零收益或其他日期代替")
    s = signals.copy()
    s["target_equity"] = pd.to_numeric(s["target_equity"], errors="coerce")
    if not s.empty:
        s["signal_at"] = s["signal_at"].map(_time)
        if s["signal_at"].isna().any():
            return _block("信号生成时刻缺失或只有日期")
        last_open = p["date"].iloc[-1] + pd.Timedelta(hours=9, minutes=30)
        s = s.loc[s["signal_at"] < last_open].copy()
        known = s["target_equity"].notna()
        s["available_at"] = s["available_at"].map(_time)
        if (known & (s["available_at"].isna() | (s["available_at"] > s["signal_at"]))).any():
            return _block("信号使用了当时不可得的数据或缺少可得时刻")
        targets = s.loc[known, "target_equity"]
        if (~np.isfinite(targets) | targets.lt(0) | targets.gt(1)).any():
            return _block("目标股票仓位必须在 0 至 1 之间")
        s = s.sort_values(["signal_at", "available_at"], kind="stable").reset_index(drop=True)
    if s.empty or not s["target_equity"].notna().any():
        return _block("本区间没有可执行的已知信号，不能把缺数当作零仓位")
    strategies = {name: _evaluate_strategy(p, s, policy=name, cost=one_way_cost)
                  for name in ("rule", "buy_hold", "fixed_50")}
    return {
        "status": "DATA_PARTIAL" if strategies["rule"]["unknown_signals"] else "OK",
        "validation_status": "not_validated", "strategies": strategies,
        "period": {"start": p["date"].iloc[0].strftime("%Y-%m-%d"),
                   "end": p["date"].iloc[-1].strftime("%Y-%m-%d"),
                   "start_at": str(p["date"].iloc[0] + pd.Timedelta(hours=9, minutes=30)),
                   "end_at": str(p["date"].iloc[-1] + pd.Timedelta(hours=9, minutes=30))},
        "assumptions": {"one_way_cost": one_way_cost, "cash_return": 0.0,
                        "execution": "信号形成后的下一可成交交易日 09:30 开盘价（上海时间）",
                        "return_basis": "调用方提供的分红再投资总收益复权开盘价",
                        "initial_portfolio": "起始全部为现金",
                        "initial_nav": 1.0,
                        "trade_value_unit": "初始净值为 1 的归一化单位，并非人民币金额",
                        "unknown_signal": "目标仓位缺失时撤销未成交指令并保留已有持仓",
                        "fixed_50": "每个可成交开盘再平衡至 50%",
                        "terminal_liquidation": False, "volatility_annualization_days": 252},
        "evidence": dict(evidence), "limitations": list(_OMISSIONS),
    }
