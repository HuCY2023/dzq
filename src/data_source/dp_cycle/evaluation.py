"""Small, evidence-gated allocation replay; observations are not recommendations."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

import numpy as np
import pandas as pd


DEFAULT_ONE_WAY_COST = 0.0005
_OMISSIONS = [
    "平均股票暴露匹配使用整个评估期的事后结果，只是描述性对照。",
    "分区外样本评价、预警标签和单项消融尚未实现，不能视为完整有效性验证。",
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
    prices: pd.DataFrame, signals: pd.DataFrame, *, policy: str, cost: float, fixed_target: float = 0.5,
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
                target = fixed_target
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


def _baseline_signals(prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """只在可成交开盘后形成基准信号；下一可成交开盘才能执行。"""
    opens: list[float] = []
    rows: dict[str, list[dict[str, Any]]] = {"trend_20": [], "volatility_20": []}
    tradable = prices.loc[prices["tradable"]]
    if tradable.empty:
        return {name: pd.DataFrame() for name in rows}
    last_open = tradable["date"].iloc[-1] + pd.Timedelta(hours=9, minutes=30)
    for bar in tradable.itertuples(index=False):
        opens.append(float(bar.total_return_open))
        timestamp = pd.Timestamp(bar.date) + pd.Timedelta(hours=9, minutes=31)
        if timestamp >= last_open:
            break
        if len(opens) >= 20:
            target = float(opens[-1] > float(np.mean(opens[-20:])))
            rows["trend_20"].append({"signal_at": timestamp, "available_at": timestamp, "target_equity": target})
        if len(opens) >= 21:
            recent = np.asarray(opens[-21:], dtype=float)
            returns = recent[1:] / recent[:-1] - 1.0
            volatility = float(np.std(returns, ddof=1) * np.sqrt(252))
            target = min(1.0, 0.15 / volatility) if volatility > 0 else 1.0
            rows["volatility_20"].append({"signal_at": timestamp, "available_at": timestamp, "target_equity": target})
    return {name: pd.DataFrame(items) for name, items in rows.items()}


def purged_time_split(
    trading_dates: Iterable[Any], *, train_end: Any, validation_end: Any,
    test_end: Any | None = None, label_horizon: int = 60,
) -> dict[str, Any]:
    """按固定边界划分标签样本；标签终点不能跨越所属分区。"""
    days = pd.DatetimeIndex(pd.to_datetime(list(trading_dates), errors="coerce")).normalize()
    if days.empty or days.isna().any() or days.has_duplicates or not days.is_monotonic_increasing:
        raise ValueError("交易日历必须按日期排序且无重复")
    if not isinstance(label_horizon, int) or label_horizon <= 0:
        raise ValueError("标签间隔必须是正整数")
    train, validation = pd.Timestamp(train_end).normalize(), pd.Timestamp(validation_end).normalize()
    test = pd.Timestamp(test_end).normalize() if test_end is not None else days[-1]
    if pd.isna(train) or pd.isna(validation) or pd.isna(test) or not train < validation < test:
        raise ValueError("训练、验证、测试边界必须依次递增")
    visible = days[days <= test]
    partitions: dict[str, list[dict[str, str]]] = {"train": [], "validation": [], "test": []}
    excluded = 0
    for i, day in enumerate(visible):
        name, end = (("train", train) if day <= train else
                     ("validation", validation) if day <= validation else ("test", test))
        if i + label_horizon >= len(visible) or visible[i + label_horizon] > end:
            excluded += 1
            continue
        partitions[name].append({"signal_date": day.date().isoformat(),
                                 "label_end": visible[i + label_horizon].date().isoformat()})
    complete = test <= days[-1] and all(partitions.values())
    return {"status": "OK" if complete else "DATA_PARTIAL", "label_horizon": label_horizon,
            "boundaries": {"train_end": train.date().isoformat(), "validation_end": validation.date().isoformat(),
                           "test_end": test.date().isoformat()},
            "partitions": partitions, "excluded_count": excluded,
            "note": "仅按交易日生成候选日期和标签边界，不代表当日已有信号；不能替代历史版本核验或分区外评价。"}


def _evidence_error(evidence: dict[str, Any]) -> str | None:
    required = ("symbol", "price_source", "calendar_source", "price_adjustment_evidence",
                "dividend_treatment", "signal_vintage_evidence")
    missing = [name for name in required if not evidence.get(name)]
    verified = ("calendar_verified", "point_in_time_verified", "execution_prices_verified", "total_return_verified")
    if (missing or evidence.get("instrument_type") not in {"ETF", "stock"}
            or evidence.get("market") != "CN_A_SHARE"
            or any(evidence.get(name) is not True for name in verified)):
        return "缺少可交易标的、交易日历、复权/分红或历史信号可得版本的证明"
    return None


def _prepare_prices(prices: pd.DataFrame, trading_dates: Iterable[Any] | None) -> tuple[pd.DataFrame | None, str | None]:
    need_prices = {"date", "actual_open", "total_return_open", "tradable"}
    if not isinstance(prices, pd.DataFrame) or prices.empty or not need_prices.issubset(prices.columns):
        return None, "缺少可交易价格或总收益复权价格"
    p = prices.copy()
    p["date"] = pd.to_datetime(p["date"], errors="coerce").dt.normalize()
    if p["date"].isna().any() or p["date"].duplicated().any():
        return None, "价格交易日缺失或重复"
    p = p.sort_values("date").reset_index(drop=True)
    calendar = pd.to_datetime(list(trading_dates) if trading_dates is not None else [], errors="coerce")
    calendar = pd.DatetimeIndex(calendar).normalize().sort_values()
    if calendar.isna().any() or calendar.has_duplicates or list(calendar) != list(p["date"]):
        return None, "交易日历与价格序列不一致"
    tradable = p["tradable"].map({True: True, False: False, 1: True, 0: False, "true": True, "false": False, "True": True, "False": False})
    if tradable.isna().any():
        return None, "缺少可成交状态"
    p["tradable"] = tradable.astype(bool)
    for col in ("actual_open", "total_return_open"):
        p[col] = pd.to_numeric(p[col], errors="coerce")
    if (p["total_return_open"].isna() | ~np.isfinite(p["total_return_open"]) | p["total_return_open"].le(0)).any() or (
        p["tradable"] & (p["actual_open"].isna() | ~np.isfinite(p["actual_open"]) | p["actual_open"].le(0))
    ).any():
        return None, "价格缺失，不能用零收益或其他日期代替"
    return p, None


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
    error = _evidence_error(evidence)
    if error:
        return _block(error)
    if not 0 <= one_way_cost < 1:
        return _block("交易成本不在有效范围")
    p, error = _prepare_prices(prices, trading_dates)
    if error:
        return _block(error)
    need_signals = {"signal_at", "available_at", "target_equity"}
    if not isinstance(signals, pd.DataFrame) or not need_signals.issubset(signals.columns):
        return _block("缺少信号生成时间、可得时间或目标仓位")
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
    for name, baseline in _baseline_signals(p).items():
        if baseline.empty:
            strategies[name] = {"status": "unavailable", "reason": "可成交开盘样本不足，无法形成并执行基准信号。"}
        else:
            strategies[name] = {"status": "OK", **_evaluate_strategy(p, baseline, policy="rule", cost=one_way_cost)}
    exposure = strategies["rule"]["metrics"]["average_equity"]
    matched = _evaluate_strategy(p, s, policy="fixed_50", cost=one_way_cost, fixed_target=exposure)
    strategies["matched_exposure"] = {"status": "OK", "target_equity": exposure,
                                     "note": "事后使用全期平均仓位匹配，仅供对照，不是当时可事先执行的策略。",
                                     **matched}
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
                        "trend_20": "最近20次可成交开盘价格与均值比较，信号形成后下一可成交开盘执行",
                        "volatility_20": "最近20个可成交开盘收益年化波动，15%目标，仓位限制0–100%",
                        "matched_exposure": "全期规则平均股票暴露的事后固定比例，仅用于描述性对照",
                        "terminal_liquidation": False, "volatility_annualization_days": 252},
        "evidence": dict(evidence), "limitations": list(_OMISSIONS),
    }


def evaluate_warning_windows(
    prices: pd.DataFrame, warnings: pd.DataFrame, evidence: dict[str, Any], *,
    trading_dates: Iterable[Any] | None, horizons: tuple[int, ...] = (20, 60),
) -> dict[str, Any]:
    """Measure non-overlapping warning windows on observed tradable opens.

    Window selection does not establish statistical independence or a forecast
    threshold; false positives and missed risks require a separate protocol.
    """
    error = _evidence_error(evidence)
    if error:
        return _block(error)
    if not evidence.get("warning_vintage_evidence"):
        return _block("缺少预警信号当时可得版本的证明")
    p, error = _prepare_prices(prices, trading_dates)
    if error:
        return _block(error)
    if not isinstance(warnings, pd.DataFrame) or not {"signal_at", "available_at", "warning"}.issubset(warnings.columns):
        return _block("预警需提供生成时间、可得时间和是否触发")
    if not horizons or any(not isinstance(h, int) or h <= 0 for h in horizons) or len(set(horizons)) != len(horizons):
        return _block("预警观察间隔必须是不同的正整数")

    w = warnings.copy()
    known = w["warning"].notna()
    active = w["warning"].map({True: True, False: False, 1: True, 0: False,
                               "true": True, "false": False, "True": True, "False": False})
    if (known & active.isna()).any():
        return _block("预警状态须为真、假或未知")
    w["signal_at"] = w["signal_at"].map(_time)
    w["available_at"] = w["available_at"].map(_time)
    if w["signal_at"].isna().any() or (known & (w["available_at"].isna() | (w["available_at"] > w["signal_at"]))).any():
        return _block("预警生成时刻或数据可得时刻不完整，或使用了未来数据")
    w["warning"] = active
    w = w.sort_values("signal_at", kind="stable").reset_index(drop=True)
    triggered = w.loc[w["warning"] == True]
    unknown_count = int((~known).sum())
    openings = p["date"] + pd.Timedelta(hours=9, minutes=30)
    result: dict[str, Any] = {}
    incomplete = False
    for horizon in horizons:
        events = []
        overlapping_count = incomplete_count = 0
        previous_end = -1
        for signal in triggered.itertuples(index=False):
            if signal.signal_at.normalize() < p.at[0, "date"]:
                incomplete_count += 1
                continue
            candidates = p.index[p["tradable"] & (openings > signal.signal_at)]
            if candidates.empty:
                incomplete_count += 1
                continue
            entry = int(candidates[0])
            if entry <= previous_end:
                overlapping_count += 1
                continue
            end = entry + horizon
            previous_end = end
            if end >= len(p) or not p.at[end, "tradable"]:
                incomplete_count += 1
                continue
            path = p.loc[entry:end]
            observed = path.loc[path["tradable"], "total_return_open"].to_numpy(dtype=float)
            drawdown = observed / np.maximum.accumulate(observed) - 1.0
            events.append({"signal_at": str(signal.signal_at), "entry_date": p.at[entry, "date"].date().isoformat(),
                           "end_date": p.at[end, "date"].date().isoformat(),
                           "forward_return": float(observed[-1] / observed[0] - 1.0),
                           "max_drawdown": float(drawdown.min())})
        incomplete |= incomplete_count > 0
        result[str(horizon)] = {"warning_count": len(triggered), "overlapping_count": overlapping_count,
                                "incomplete_count": incomplete_count, "events": events}
    return {
        "status": "DATA_PARTIAL" if unknown_count or incomplete else "OK",
        "validation_status": "not_validated", "unknown_count": unknown_count, "horizons": result,
        "note": "每个间隔按触发时间保留不重叠窗口，不保证统计独立；仅用可成交开盘价观察，"
                "不含盘中波动；价格及历史版本证据由调用方提供，不能由此推断漏报或预警有效性。",
    }
