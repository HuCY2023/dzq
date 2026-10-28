# -*- coding: utf-8 -*-
"""大周期五项指标 · 长期跟踪正式 PDF。"""

from __future__ import annotations

import os
import shutil
import textwrap
from datetime import datetime
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages

from auto_report.report_branding import DISCLAIMER_BODY_CN
from core.plotting import setup_matplotlib_chinese

PAGE = (11.69, 8.27)  # A4 landscape


def _page(fig: plt.Figure, pdf: PdfPages) -> None:
    pdf.savefig(fig, dpi=150)
    plt.close(fig)


def _footer(ax: plt.Axes, page: int, total: int, as_of: str) -> None:
    ax.text(
        0.5,
        0.02,
        f"大周期五项指标 · 长期跟踪  |  分析日期 {as_of}  |  各项数据日期见明细  |  {page}/{total}",
        transform=ax.figure.transFigure,
        ha="center",
        va="bottom",
        fontsize=8,
        color="#666",
    )


def _cover(pdf: PdfPages, *, as_of: str, diagnosis: dict[str, Any], total: int) -> None:
    """首页突出：结论、总分、仓位、关键节点。"""
    fig = plt.figure(figsize=PAGE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.add_patch(plt.Rectangle((0, 0.78), 1, 0.22, color="#0f2744", transform=ax.transAxes, clip_on=False))
    ax.text(0.05, 0.90, "大周期 · 散户决策", fontsize=26, color="white", fontweight="bold", transform=ax.transAxes)
    ax.text(0.05, 0.83, f"视频全量口径 · 长期跟踪  ·  分析日期 {as_of}", fontsize=12, color="#c5d4e8", transform=ax.transAxes)

    pos = diagnosis.get("position") or {}
    dec = diagnosis.get("decision") or {}
    score = diagnosis.get("score")
    score_txt = "—" if score is None else f"{int(score):+d}"
    tone = "#7a2e22" if isinstance(score, (int, float)) and score < 0 else "#0f2744"

    ax.text(
        0.05,
        0.72,
        dec.get("label") or diagnosis.get("state") or "—",
        fontsize=22,
        fontweight="bold",
        color=tone,
        transform=ax.transAxes,
    )
    ax.text(0.05, 0.66, diagnosis.get("state") or "", fontsize=12, color="#555", transform=ax.transAxes)
    ax.text(0.05, 0.61, diagnosis.get("summary") or "", fontsize=11, color="#333", transform=ax.transAxes)
    ax.text(
        0.05,
        0.55,
        f"仓位 {dec.get('equity_text') or pos.get('equity_text') or '—'}  ·  "
        f"{pos.get('actor') or ''}  ·  {pos.get('sleeve') or ''}",
        fontsize=11,
        color="#222",
        transform=ax.transAxes,
    )
    k_type = diagnosis.get("k_type") or {}
    if k_type.get("hint"):
        ax.text(
            0.05,
            0.50,
            f"K型：{k_type.get('mode') or ''} — {k_type.get('hint')}",
            fontsize=9,
            color="#555",
            transform=ax.transAxes,
        )

    ax.add_patch(plt.Rectangle((0.72, 0.52), 0.23, 0.20, facecolor="#f5f7fa", edgecolor="#d0d7e2", transform=ax.transAxes))
    ax.text(0.835, 0.68, "总分", fontsize=10, ha="center", color="#666", transform=ax.transAxes)
    ax.text(0.835, 0.58, score_txt, fontsize=32, ha="center", fontweight="bold", color=tone, transform=ax.transAxes)
    ax.text(0.05, 0.45, diagnosis.get("score_note") or "", fontsize=9, color="#555", transform=ax.transAxes)
    ax.text(0.05, 0.41, diagnosis.get("score_logic") or "", fontsize=8, color="#666", transform=ax.transAxes)

    red = diagnosis.get("red_light") or {}
    ax.text(
        0.05,
        0.36,
        red.get("note") or "红灯四件套：偏离 · 高低比 · 高杠杆 · 外盘",
        fontsize=9,
        color="#7a2e22" if red.get("severe") else "#555",
        transform=ax.transAxes,
    )

    ax.text(0.05, 0.31, "三种系统性风险", fontsize=12, fontweight="bold", color="#0f2744", transform=ax.transAxes)
    nodes = diagnosis.get("nodes") or []
    cols = 3
    for i, nd in enumerate(nodes):
        r, c = divmod(i, cols)
        x0 = 0.05 + c * 0.31
        y0 = 0.22 - r * 0.18
        st = nd.get("status") or "gap"
        edge = {"on": "#c45c26", "watch": "#c9a227", "off": "#2f5a42", "gap": "#d0d7e2"}.get(st, "#d0d7e2")
        face = {"on": "#f8e8e4", "watch": "#f7f0d8", "off": "#e8f3ec", "gap": "#f5f7fa"}.get(st, "#f5f7fa")
        ax.add_patch(plt.Rectangle((x0, y0 - 0.08), 0.29, 0.16, facecolor=face, edgecolor=edge, transform=ax.transAxes))
        ax.text(x0 + 0.01, y0 + 0.05, nd.get("title") or "", fontsize=10, fontweight="bold", color="#222", transform=ax.transAxes)
        ax.text(x0 + 0.01, y0 + 0.015, textwrap.fill(nd.get("text") or "", 30), va="top", fontsize=8, color="#444", transform=ax.transAxes)

    _footer(ax, 1, total, as_of)
    _page(fig, pdf)


def _score_page(
    pdf: PdfPages, *, as_of: str, diagnosis: dict[str, Any],
    items: list[dict[str, Any]], page: int, total: int,
) -> None:
    fig = plt.figure(figsize=PAGE)
    ax = fig.add_axes([0.04, 0.08, 0.92, 0.86])
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.text(0.0, 0.96, "打分明细 · 每项规则", fontsize=16, fontweight="bold", color="#0f2744", transform=ax.transAxes)
    ax.text(0.0, 0.91, textwrap.fill(diagnosis.get("score_logic") or "", 100), fontsize=8, color="#555", transform=ax.transAxes)

    y = 0.86
    ax.text(0.0, y, "项", fontsize=8, color="#666", transform=ax.transAxes)
    ax.text(0.10, y, "分", fontsize=8, color="#666", transform=ax.transAxes)
    ax.text(0.16, y, "当前读数", fontsize=8, color="#666", transform=ax.transAxes)
    ax.text(0.58, y, "打分规则", fontsize=8, color="#666", transform=ax.transAxes)
    y -= 0.035
    for it in items:
        sc = it.get("score")
        status_text = {"info": "观察", "stale": "陈旧", "partial": "部分覆盖"}.get(it.get("status"))
        sc_txt = status_text or ("缺" if sc is None else f"{int(sc):+d}")
        tone = "#7a2e22" if isinstance(sc, (int, float)) and sc < 0 else ("#0f6e56" if isinstance(sc, (int, float)) and sc > 0 else "#666")
        ax.text(0.0, y, it.get("label") or "", fontsize=10, fontweight="bold", color="#222", transform=ax.transAxes)
        ax.text(0.10, y, sc_txt, fontsize=11, fontweight="bold", color=tone, transform=ax.transAxes)
        ax.text(0.16, y, textwrap.fill((it.get("note") or "").replace("≫", ">>"), 38), va="top", fontsize=8, color="#333", transform=ax.transAxes)
        ax.text(0.58, y, textwrap.fill(it.get("rule") or "", 38), va="top", fontsize=8, color="#555", transform=ax.transAxes)
        y -= 0.09

    red = diagnosis.get("red_light") or {}
    flags = red.get("flags") or []
    if flags:
        bits = " · ".join(
            f"{f.get('label')}({'未知' if f.get('status') == 'unknown' or f.get('on') is None else '亮' if f.get('on') else '灭'})" for f in flags
        )
        ax.text(0.0, 0.16, "红灯：" + bits, fontsize=8, color="#7a2e22", transform=ax.transAxes)
    gaps = diagnosis.get("gaps") or []
    if gaps:
        ax.text(0.0, 0.11, textwrap.fill("缺口：" + "；".join(str(g) for g in gaps[:4]), 100), va="top", fontsize=8, color="#666", transform=ax.transAxes)
    _footer(ax, page, total, as_of)
    _page(fig, pdf)


def _dashboard(pdf: PdfPages, *, as_of: str, cards: list[dict[str, Any]], total: int, page: int) -> None:
    fig = plt.figure(figsize=PAGE)
    ax = fig.add_axes([0.04, 0.08, 0.92, 0.86])
    ax.axis("off")
    ax.set_title("仪表盘摘要（Dashboard）", fontsize=16, loc="left", pad=12, color="#0f2744")

    n = len(cards)
    cols = 3
    rows = (n + cols - 1) // cols
    w, h = 0.30, 0.32
    for i, c in enumerate(cards):
        r, col = divmod(i, cols)
        x0 = 0.02 + col * (w + 0.04)
        y0 = 0.95 - (r + 1) * (h + 0.06)
        box = plt.Rectangle((x0, y0), w, h, fill=True, facecolor="#f5f7fa", edgecolor="#d0d7e2", transform=ax.transAxes)
        ax.add_patch(box)
        ax.text(x0 + 0.015, y0 + h - 0.05, c["title"], fontsize=10, fontweight="bold", color="#0f2744", transform=ax.transAxes)
        ax.text(x0 + 0.015, y0 + h - 0.12, c["value"], fontsize=14, fontweight="bold", color=c.get("color", "#222"), transform=ax.transAxes)
        ax.text(x0 + 0.015, y0 + 0.06, c.get("sub", ""), fontsize=8, color="#666", transform=ax.transAxes, wrap=True)
        _ = rows  # quiet

    _footer(ax, page, total, as_of)
    _page(fig, pdf)


def _chart_page(pdf: PdfPages, png_path: str, *, title: str, blurb: str, as_of: str, page: int, total: int) -> None:
    from matplotlib import image as mpimg

    fig = plt.figure(figsize=PAGE)
    ax_t = fig.add_axes([0.04, 0.90, 0.92, 0.08])
    ax_t.axis("off")
    ax_t.text(0, 0.55, title, fontsize=14, fontweight="bold", color="#0f2744", transform=ax_t.transAxes)
    ax_t.text(0, 0.05, blurb, fontsize=8, color="#555", transform=ax_t.transAxes)

    ax = fig.add_axes([0.04, 0.08, 0.92, 0.80])
    if png_path and os.path.isfile(png_path):
        ax.imshow(mpimg.imread(png_path))
    else:
        ax.text(0.5, 0.5, "图表缺失", ha="center", va="center")
    ax.axis("off")
    _footer(ax, page, total, as_of)
    _page(fig, pdf)


def _position_page(pdf: PdfPages, *, as_of: str, diagnosis: dict[str, Any], page: int, total: int) -> None:
    fig = plt.figure(figsize=PAGE)
    ax = fig.add_axes([0.04, 0.08, 0.92, 0.86])
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    pos = diagnosis.get("position") or {}
    score = diagnosis.get("score")
    score_text = "—" if score is None else f"{int(score):+d}"
    ax.text(0.0, 0.96, "仓位带 · 资金行为路径", fontsize=16, fontweight="bold", color="#0f2744", transform=ax.transAxes)
    ax.text(
        0.0,
        0.90,
        f"{diagnosis.get('state') or '—'}    可见项 {score_text}    当前 {pos.get('equity_text') or '—'}  ·  {pos.get('actor') or ''}",
        fontsize=11,
        color="#222",
        transform=ax.transAxes,
    )
    ax.text(0.0, 0.85, str(pos.get("sleeve") or ""), fontsize=9, color="#444", transform=ax.transAxes)

    phases = [p for p in (diagnosis.get("phases") or [])]
    n = max(len(phases), 1)
    w = 0.92 / n
    for i, ph in enumerate(phases):
        x0 = 0.0 + i * w
        color = "#f8e8e4" if ph.get("side") == "sell" else "#eef2f6"
        edge = "#c45c26" if ph.get("active") else "#d0d7e2"
        ax.add_patch(
            plt.Rectangle((x0 + 0.004, 0.70), w - 0.01, 0.12, facecolor=color, edgecolor=edge, linewidth=1.4, transform=ax.transAxes)
        )
        ax.text(x0 + 0.01, 0.785, ph.get("actor") or "", fontsize=8, color="#7a2e22" if ph.get("side") == "sell" else "#222", transform=ax.transAxes)
        ax.text(x0 + 0.01, 0.745, f"{ph.get('stage') or ''}  {ph.get('equity_text') or ''}", fontsize=7, color="#555", transform=ax.transAxes)

    ax.text(0.0, 0.64, "有直接序列的阶段（融资、偏离）。国家队和散户没有序列，不填进历史。", fontsize=10, color="#0f2744", transform=ax.transAxes)
    named = list(diagnosis.get("periods") or [])
    mid = (len(named) + 1) // 2
    columns = [named[:mid], named[mid:]]
    for col, segs in enumerate(columns):
        x = 0.0 if col == 0 else 0.52
        y = 0.59
        ax.text(x, y, "起止", fontsize=7, color="#666", transform=ax.transAxes)
        ax.text(x + 0.28, y, "阶段 / 仓位", fontsize=7, color="#666", transform=ax.transAxes)
        y -= 0.028
        for seg in segs:
            tone = "#7a2e22" if seg.get("side") == "sell" else "#222"
            ax.text(x, y, f"{seg.get('start')} → {seg.get('end')}", fontsize=7, color="#333", transform=ax.transAxes)
            ax.text(
                x + 0.28,
                y,
                f"{seg.get('stage') or ''}  {seg.get('equity_text') or ''}",
                fontsize=7,
                color=tone,
                transform=ax.transAxes,
            )
            y -= 0.028
            if y < 0.08:
                break
    _footer(ax, page, total, as_of)
    _page(fig, pdf)


def _method_page(pdf: PdfPages, *, as_of: str, page: int, total: int, rule_version: str = "improved_v1",
                 holder_note: str = "", turnover_note: str = "") -> None:
    fig = plt.figure(figsize=PAGE)
    ax = fig.add_axes([0.06, 0.08, 0.88, 0.86])
    ax.axis("off")
    ax.set_title("口径 · 数据源 · 使用说明", fontsize=16, loc="left", color="#0f2744", pad=10)

    author = rule_version == "author_v1"
    lines = [
        "视频口径：资金侧边际变差共振 → 打分 → 散户五档决策。日常 ±1/±2；多项同负才极端（如 −9）。",
        "A 资金三件套：主力（只作风控）· 宽基ETF份额代理（不可识别持有人）· 融资分位×方向。",
        "B 行为：成长–价值偏离≈17；高低比热~10:1 / 空~1:10。本机广度默认乐咕120日，≠作者52周。",
        "C 解禁：提前约一年；高峰后 2–3 月主力易偏高。",
        ("D 扩展：剪刀差可计分；公募、减持、行业项按数据覆盖判断。" if author else
         "D 扩展：剪刀差仅观察，公募新发仅观察；减持和行业按覆盖判断。"),
        "E 旁证：A股成交拥挤度、科创IV、美股保证金/M2（默认不进合计分）。",
        "红灯四件套：偏离极值 + 高低比背离 + 高杠杆 + 外盘脆弱；扳机仍是资金边际。",
        ("决策：关键数据不足不输出仓位；作者版允许ETF份额代理参与仓位。" if author else
         "决策：关键数据不足不输出仓位；ETF份额代理不独立决定仓位。"),
        "权威：research/DP/大周期/框架.md · 散户决策_推演核查.md",
        "",
        "更新报告：双击 update-local.cmd，或运行 scripts/build_web_report.py --refresh --pdf。",
        "稳定 PDF：output/user/dp_cycle/大周期五项指标_长期跟踪.pdf",
        "",
        "免责声明：",
        DISCLAIMER_BODY_CN,
    ]
    if holder_note:
        lines.insert(2, holder_note)
    if turnover_note:
        lines.insert(3, turnover_note)
    y = 0.92
    for line in lines:
        ax.text(0.0, y, line, fontsize=9, color="#333", transform=ax.transAxes, va="top")
        y -= 0.055 if line else 0.03
    _footer(ax, page, total, as_of)
    _page(fig, pdf)


def _fmt_num(x: Any, nd: int = 2) -> str:
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return "—"
        return f"{float(x):,.{nd}f}"
    except Exception:
        return str(x)


def build_long_term_pdf(
    *,
    out_pdf: str,
    meta: dict[str, Any],
    pngs: dict[str, str],
    tables: dict[str, pd.DataFrame] | None = None,
    diagnosis: dict[str, Any] | None = None,
    latest_copy: str | None = None,
) -> str:
    """生成正式长期跟踪 PDF；可选同步到稳定路径。"""
    setup_matplotlib_chinese()
    tables = tables or {}
    ind = meta.get("indicators") or {}
    as_of = (tables.get("meta") or {}).get("as_of") or datetime.now().strftime("%Y-%m-%d")

    mf = ind.get("01_main_force") or {}
    etf = ind.get("02_national_team") or {}
    mg = ind.get("03_margin") or {}
    gv = ind.get("04_growth_value") or {}
    hl = ind.get("05_high_low") or {}
    from data_source.dp_cycle.gui_bundle import _high_low_captions

    breadth_captions = _high_low_captions(tables.get("high_low"))

    # ETF 最新合计
    etf_sum = "—"
    etf_df = tables.get("etf_daily")
    if etf_df is not None and not etf_df.empty:
        last_d = pd.to_datetime(etf_df["date"]).max()
        etf_sum = _fmt_num(etf_df.loc[pd.to_datetime(etf_df["date"]) == last_d, "份额_亿份"].sum(), 2) + " 亿份"

    all_ready = all(v.get("ok") and not v.get("partial") for v in ind.values())
    etf_coverage = etf.get("coverage") or {}
    etf_sub = "≠汇金/证金身份"
    if etf_coverage.get("current_count") is not None:
        etf_sub = f"覆盖 {etf_coverage['current_count']}/{etf_coverage.get('expected_count', '?')} 只 · " + etf_sub
    etf_flow = tables.get("etf_flow") or {}
    flow_coverage = etf_flow.get("coverage") or {}
    if etf_flow.get("date") and etf_flow.get("flow_yi") is not None:
        etf_sum = f"{float(etf_flow['flow_yi']):+.2f} 亿（估算）"
        etf_sub = (f"{etf_flow['date']} · {flow_coverage.get('comparable', 0)}/"
                   f"{flow_coverage.get('expected', 4)}只 · 非实际现金流")
        if (etf_flow.get("source") or {}).get("events_recorded") is False:
            etf_sub += " · 事件表未留存"
    cards = [
        {
            "title": "01 主力资金",
            "value": f"{_fmt_num(mf.get('today_净额_亿元'))} 亿",
            "sub": "东财大盘大单净额 · 历史靠增量库",
            "color": "#f4664a" if (mf.get("today_净额_亿元") or 0) < 0 else "#0f6e56",
        },
        {
            "title": "02 ETF 份额代理",
            "value": etf_sum,
            "sub": etf_sub,
            "color": "#0f2744",
        },
        {
            "title": "03 融资余额",
            "value": f"{_fmt_num(mg.get('last_融资余额_亿元'), 0)} 亿",
            "sub": "融资余额 · 截至当期历史分位",
            "color": "#0f2744",
        },
        {
            "title": "04 偏离度",
            "value": _fmt_num(gv.get("last_偏离度")),
            "sub": "对照作者经验值 17",
            "color": "#0f2744",
        },
        {
            "title": "05 广度",
            "value": f"{hl.get('last_新高', '—')}−{hl.get('last_新低', '—')}",
            "sub": breadth_captions[2],
            "color": "#f4664a" if int(hl.get("last_新低") or 0) > int(hl.get("last_新高") or 0) else "#0f6e56",
        },
        {
            "title": "数据状态",
            "value": "五项就绪" if all_ready else "部分覆盖或缺失",
            "sub": "详见 scan_meta.txt",
            "color": "#0f6e56" if all_ready else "#f4664a",
        },
    ]

    chart_specs = [
        ("01_main_force", "01 主力资金", "东财大单+超大单净额 · 月/周；作者线仅对照"),
        ("02_national_team", "02 核心宽基ETF份额代理", "宽基 ETF 份额变动 · 不能识别持有人"),
        ("03_margin", "03 杠杆资金（两融）", "沪深融资余额 · 月变化 · 历史分位"),
        ("04_growth_value", "04 成长–价值偏离度", "(PE比+PB比)/2 · 创业板50 vs 上证50"),
        ("05_high_low", breadth_captions[2], breadth_captions[1]),
    ]
    from data_source.dp_cycle.diagnosis import diagnose_position

    if diagnosis is None:
        diagnosis = diagnose_position(tables, heavy=True, as_of=as_of)
    items = diagnosis["items"]
    score_groups = [items[i:i + 7] for i in range(0, len(items), 7)]
    # cover + score pages + position + dashboard + charts + method
    total = 4 + len(score_groups) + len(chart_specs)

    os.makedirs(os.path.dirname(out_pdf) or ".", exist_ok=True)
    with PdfPages(out_pdf) as pdf:
        _cover(pdf, as_of=str(as_of), diagnosis=diagnosis, total=total)
        for page, score_items in enumerate(score_groups, start=2):
            _score_page(pdf, as_of=str(as_of), diagnosis=diagnosis, items=score_items, page=page, total=total)
        _position_page(pdf, as_of=str(as_of), diagnosis=diagnosis, page=2 + len(score_groups), total=total)
        _dashboard(pdf, as_of=str(as_of), cards=cards, total=total, page=3 + len(score_groups))
        for i, (key, title, blurb) in enumerate(chart_specs, start=4 + len(score_groups)):
            _chart_page(
                pdf,
                pngs.get(key, ""),
                title=title,
                blurb=blurb,
                as_of=str(as_of),
                page=i,
                total=total,
            )
        turnover = tables.get("turnover_observation") or {}
        turnover_note = ""
        if turnover.get("date"):
            turnover_note = (f"本地成交 {turnover['date']}：{turnover['stocks']} 只，前5%占 "
                             f"{turnover['shares_pct']['5']:.2f}%，{turnover['top5_amount_yi']:.2f} 亿元；部分覆盖，仅观察。")
        _method_page(pdf, as_of=str(as_of), page=total, total=total,
                     rule_version=diagnosis.get("rule_version") or "improved_v1",
                     holder_note=(tables.get("etf_holder_disclosures") or {}).get("pdf_note", ""), turnover_note=turnover_note)

    if latest_copy:
        os.makedirs(os.path.dirname(latest_copy) or ".", exist_ok=True)
        shutil.copy2(out_pdf, latest_copy)

    return out_pdf
