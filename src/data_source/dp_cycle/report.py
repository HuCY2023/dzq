# -*- coding: utf-8 -*-
"""五项指标图表面板。"""

from __future__ import annotations

import os
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd

from core.plotting import setup_matplotlib_chinese


def _save(fig: plt.Figure, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_main_force(agg: dict[str, pd.DataFrame], out_path: str) -> str | None:
    setup_matplotlib_chinese()
    monthly = agg.get("monthly", pd.DataFrame())
    weekly = agg.get("weekly", pd.DataFrame())
    daily = agg.get("daily", pd.DataFrame())
    if monthly.empty and weekly.empty and daily.empty:
        return None
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), constrained_layout=True)
    fig.suptitle("01 主力资金（THS 行业净额加总 · 本地增量）", fontsize=13)

    if not monthly.empty:
        ax = axes[0]
        ax.bar(monthly["date"], monthly["净额_亿元"], width=20, color="#5b8ff9", alpha=0.85)
        if "作者月基准线" in monthly.columns:
            ax.axhline(float(monthly["作者月基准线"].iloc[0]), color="#f4664a", ls="--", lw=1, label="作者月基准 −4000")
        ax.set_ylabel("亿元")
        ax.set_title("月度净额")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(True, alpha=0.3)

    if not weekly.empty:
        ax = axes[1]
        ax.bar(weekly["date"], weekly["净额_亿元"], width=4, color="#5ad8a6", alpha=0.85)
        if "作者周警戒线" in weekly.columns:
            ax.axhline(float(weekly["作者周警戒线"].iloc[0]), color="#f4664a", ls="--", lw=1, label="作者周警戒 −2000")
        ax.set_ylabel("亿元")
        ax.set_title("周度净额")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(True, alpha=0.3)

    if not daily.empty:
        ax = axes[2]
        tail = daily.tail(60)
        ax.plot(tail["date"], tail["净额_亿元"], color="#5b8ff9", lw=1.4)
        ax.axhline(0, color="#999", lw=0.8)
        ax.set_ylabel("亿元")
        ax.set_title("日度净额（近60条增量）")
        ax.grid(True, alpha=0.3)

    return _save(fig, out_path)


def plot_national_team(hist: pd.DataFrame, monthly: pd.DataFrame, out_path: str) -> str | None:
    setup_matplotlib_chinese()
    if hist.empty and monthly.empty:
        return None
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), constrained_layout=True)
    fig.suptitle("02 国家队代理：核心沪深300 ETF 份额（申赎代理≠汇金/证金）", fontsize=12)

    ax = axes[0]
    if not hist.empty:
        for code, g in hist.groupby("code"):
            ax.plot(g["date"], g["份额_亿份"], label=f"{code}", lw=1.3)
        ax.set_ylabel("亿份")
        ax.set_title("日度份额（本地增量库）")
        ax.legend(fontsize=8, ncol=2)
        ax.grid(True, alpha=0.3)

    ax = axes[1]
    if not monthly.empty:
        colors = ["#f4664a" if v < 0 else "#5ad8a6" for v in monthly["月变动_亿份"].fillna(0)]
        ax.bar(monthly["month_start"], monthly["月变动_亿份"], width=20, color=colors, alpha=0.85)
        ax.axhline(0, color="#999", lw=0.8)
        ax.set_ylabel("亿份")
        ax.set_title("月末合计份额 · 月变动")
        ax.grid(True, alpha=0.3)

    return _save(fig, out_path)


def plot_margin(df: pd.DataFrame, out_path: str) -> str | None:
    setup_matplotlib_chinese()
    if df.empty:
        return None
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), constrained_layout=True)
    fig.suptitle("03 杠杆资金：沪深融资余额", fontsize=13)
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    if "月变化_亿元" not in d.columns and "融资余额_亿元" in d.columns:
        s = d.dropna(subset=["融资余额_亿元"]).set_index("date")["融资余额_亿元"].sort_index()
        chg = s.resample("ME").last().diff()
        month_end = d["date"].dt.to_period("M").dt.to_timestamp("M")
        d["月变化_亿元"] = month_end.map(chg)
        last_day = d.groupby(d["date"].dt.to_period("M"))["date"].transform("max")
        d.loc[d["date"] != last_day, "月变化_亿元"] = pd.NA

    ax = axes[0]
    ax.plot(d["date"], d["融资余额_亿元"], color="#5b8ff9", lw=1.2, label="融资余额")
    if "两融余额_亿元" in d.columns:
        ax.plot(d["date"], d["两融余额_亿元"], color="#9270ca", lw=1.0, alpha=0.8, label="两融余额")
    ax.set_ylabel("亿元")
    ax.set_title("日度余额")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    m = d.dropna(subset=["月变化_亿元"])
    if not m.empty:
        colors = ["#f4664a" if v < 0 else "#5ad8a6" for v in m["月变化_亿元"]]
        ax.bar(m["date"], m["月变化_亿元"], width=15, color=colors, alpha=0.85)
    ax.axhline(0, color="#999", lw=0.8)
    ax.set_ylabel("亿元")
    ax.set_title("月末融资余额变化")
    ax.grid(True, alpha=0.3)

    ax = axes[2]
    ax.plot(d["date"], d["分位_全样本"], color="#5b8ff9", lw=1.1, label="全样本分位%")
    if "分位_近N年" in d.columns:
        ax.plot(d["date"], d["分位_近N年"], color="#f6bd16", lw=1.1, label="近N年分位%")
    ax.set_ylabel("%")
    ax.set_ylim(0, 100)
    ax.set_title("历史分位（窗口见 scan_meta）")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    return _save(fig, out_path)


def plot_growth_value(df: pd.DataFrame, out_path: str) -> str | None:
    setup_matplotlib_chinese()
    if df.empty:
        return None
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), constrained_layout=True)
    fig.suptitle("04 成长价值偏离度（创业板50 / 上证50）", fontsize=13)
    d = df.copy()

    ax = axes[0]
    ax.plot(d["date"], d["PE比"], label="PE比", lw=1.2)
    ax.plot(d["date"], d["PB比"], label="PB比", lw=1.2)
    ax.legend(fontsize=8)
    ax.set_title("PE比 / PB比")
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    ax.plot(d["date"], d["偏离度"], color="#5b8ff9", lw=1.3, label="(PE比+PB比)/2")
    if "作者阈值_17" in d.columns:
        ax.axhline(float(d["作者阈值_17"].iloc[0]), color="#f4664a", ls="--", lw=1, label="作者经验值 17")
    ax.legend(fontsize=8)
    ax.set_title("偏离度")
    ax.grid(True, alpha=0.3)

    return _save(fig, out_path)


def plot_high_low(df: pd.DataFrame, out_path: str) -> str | None:
    setup_matplotlib_chinese()
    if df.empty:
        return None
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), constrained_layout=True)
    fig.suptitle("05 52周新高−新低（本地K线湖）", fontsize=13)
    d = df.copy()

    ax = axes[0]
    if "index_close" in d.columns and d["index_close"].notna().any():
        ax.plot(d["date"], d["index_close"], color="#5b8ff9", lw=1.2)
        ax.set_ylabel("指数")
        ax.set_title("对照：科创综指收盘")
    else:
        ax.text(0.5, 0.5, "无指数对照", ha="center", transform=ax.transAxes)
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    ax.plot(d["date"], d["new_high"], color="#f4664a", lw=1.1, label="52周新高家数")
    ax.plot(d["date"], d["new_low"], color="#5ad8a6", lw=1.1, label="52周新低家数")
    ax.legend(fontsize=8)
    ax.set_title("家数")
    ax.grid(True, alpha=0.3)

    ax = axes[2]
    ax.plot(d["date"], d["新高-新低"], color="#9270ca", lw=1.2)
    ax.axhline(0, color="#999", lw=0.8)
    ax.set_title("新高 − 新低")
    ax.grid(True, alpha=0.3)

    return _save(fig, out_path)


def write_pdf(png_paths: list[str], pdf_path: str) -> str | None:
    """将多页 PNG 拼成 PDF。"""
    paths = [p for p in png_paths if p and os.path.isfile(p)]
    if not paths:
        return None
    try:
        from matplotlib.backends.backend_pdf import PdfPages
        from matplotlib import image as mpimg

        setup_matplotlib_chinese()
        with PdfPages(pdf_path) as pdf:
            for p in paths:
                img = mpimg.imread(p)
                fig, ax = plt.subplots(figsize=(11, 8.5))
                ax.imshow(img)
                ax.axis("off")
                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)
        return pdf_path
    except Exception:
        return None


def write_meta(path: str, meta: dict[str, Any]) -> None:
    import json

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
