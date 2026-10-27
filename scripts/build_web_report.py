#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build offline web_snapshot (LWC HTML) + optional PDF."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARE_EVENTS_PATH = ROOT / "research" / "DP" / "data" / "etf_share_events.csv"
HOLDER_DISCLOSURES_PATH = ROOT / "research" / "DP" / "data" / "etf_holder_disclosures.csv"
for p in (ROOT, ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _new_run_dir(dated_dir: Path) -> Path:
    root = dated_dir / "runs"
    root.mkdir(parents=True, exist_ok=True)
    label = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    number = 0
    while True:
        candidate = root / (label if number == 0 else f"{label}-{number}")
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            number += 1


def _read_share_events() -> bytes | None:
    try:
        return SHARE_EVENTS_PATH.read_bytes()
    except FileNotFoundError:
        return None


def _save_inputs(run_dir: Path, bundle: dict, share_events: bytes | None, holder_disclosures: bytes) -> None:
    """保留生成时使用的输入，而不触碰用户原始缓存。"""
    import pandas as pd

    root = run_dir / "inputs"
    root.mkdir()
    if share_events is not None:
        (root / "etf_share_events.csv").write_bytes(share_events)
    (root / "etf_holder_disclosures.csv").write_bytes(holder_disclosures)
    for name in ("weekly", "main_force_daily", "main_force_weekly", "main_force_monthly",
                 "northbound_daily", "etf_hist", "etf_hist_adjusted", "etf_daily", "etf_monthly",
                 "margin", "growth_value", "high_low", "unlock_daily",
                 "fund_issue", "holder_reduce", "industry_flow_daily"):
        value = bundle.get(name)
        if isinstance(value, pd.DataFrame):
            value.to_csv(root / f"{name}.csv", index=False, encoding="utf-8-sig")
    for name in ("trade_calendar", "us_crowding", "etf_flow", "etf_holder_disclosures"):
        value = bundle.get(name)
        if isinstance(value, dict):
            (root / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8")
    flow = bundle.get("etf_flow") or {}
    source = (flow.get("source") or {}).get("snapshot") if isinstance(flow, dict) else None
    if source:
        snapshot = Path(source)
        for name in ("metadata.json", "source_components.csv", "components.csv", "daily.csv", "etf_share_events.csv",
                     "trade_calendar_source.csv", "index_crosscheck_source.csv"):
            if (snapshot / name).is_file():
                target = root / "etf_flow" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(snapshot / name, target)


def _publish(run_dir: Path, archived_web: Path, store: Path, dated: Path, pdf: Path | None) -> tuple[Path, Path | None]:
    """所有产物生成后切换入口；不可变的网页资源先行，HTML 最后切换。"""
    required = ("index.html", "lightweight-charts.js", "styles.css", "app.js")
    if any(not (archived_web / name).is_file() for name in required):
        raise RuntimeError("网页快照缺少运行资源")
    stable_web = store / "web_snapshot"
    version = stable_web / "versions" / run_dir.name
    version.mkdir(parents=True, exist_ok=True)
    for name in ("lightweight-charts.js", "styles.css", "app.js"):
        source = archived_web / name
        if source.is_file():
            shutil.copy2(source, version / name)

    html = (archived_web / "index.html").read_text(encoding="utf-8")
    for name in ("lightweight-charts.js", "styles.css", "app.js"):
        html = html.replace(f'"{name}"', f'"versions/{run_dir.name}/{name}"')
    prepared_html = run_dir / "latest_index.html"
    prepared_html.write_text(html, encoding="utf-8")

    latest_pdf = store / "大周期五项指标_长期跟踪.pdf" if pdf else None
    dated_pdf = dated / "大周期五项指标_长期跟踪.pdf" if pdf else None
    targets: list[tuple[Path, Path]] = []
    if pdf is not None:
        targets.extend([(pdf, dated_pdf), (pdf, latest_pdf)])  # type: ignore[list-item]
    targets.append((prepared_html, stable_web / "index.html"))
    backup_dir = run_dir / "previous_entries"
    backup_dir.mkdir()
    prepared: list[tuple[Path, Path, Path | None]] = []
    changed: list[tuple[Path, Path | None]] = []
    try:
        for number, (source, target) in enumerate(targets):
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = None
            if target.is_file():
                if target == stable_web / "index.html":
                    backup = backup_dir / "web_snapshot" / "index.html"
                    previous_html = target.read_text(encoding="utf-8")
                    for ref in re.findall(r'(?:src|href)="([^"]+)"', previous_html):
                        if Path(ref).name not in ("lightweight-charts.js", "styles.css", "app.js"):
                            continue
                        asset = stable_web / ref
                        if asset.is_file():
                            saved = backup.parent / ref
                            saved.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(asset, saved)
                else:
                    prefix = "dated" if target.parent == dated else "latest"
                    backup = backup_dir / f"{prefix}-{target.name}"
                backup.parent.mkdir(parents=True, exist_ok=True)
            if backup:
                shutil.copy2(target, backup)
            temporary = target.with_name(f".{target.name}.{run_dir.name}.tmp")
            shutil.copy2(source, temporary)
            prepared.append((target, temporary, backup))
        for target, temporary, backup in prepared:
            os.replace(temporary, target)
            changed.append((target, backup))
    except Exception:
        for target, backup in reversed(changed):
            if backup is None:
                target.unlink(missing_ok=True)
            else:
                temporary = target.with_name(f".{target.name}.{run_dir.name}.restore")
                shutil.copy2(backup, temporary)
                os.replace(temporary, target)
        raise
    finally:
        for _, temporary, _ in prepared:
            temporary.unlink(missing_ok=True)
    return stable_web / "index.html", latest_pdf


def main() -> int:
    ap = argparse.ArgumentParser(description="dzq · 网页/PDF 报告")
    ap.add_argument("--index", default="sh000001")
    ap.add_argument("--refresh", action="store_true", help="先更新入库再出报告")
    ap.add_argument("--pdf", action="store_true", help="同时生成长期跟踪 PDF")
    ap.add_argument("--rule-version", choices=("improved_v1", "author_v1"), default="improved_v1")
    args = ap.parse_args()

    from data_source.dp_cycle.gui_bundle import (
        INDEX_CHOICES,
        _clip_bundle_as_of,
        bundle_to_web_payload,
        load_dp_cycle_gui_bundle,
        write_web_snapshot,
    )

    label = next((k for k, v in INDEX_CHOICES.items() if v == args.index), args.index)
    share_events = _read_share_events()
    holder_disclosures = HOLDER_DISCLOSURES_PATH.read_bytes()
    bundle = load_dp_cycle_gui_bundle(
        index_symbol=args.index,
        refresh=args.refresh,
        skip_high_low=True,
    )
    weekly = (bundle.get("meta") or {}).get("weekly") or {}
    if not weekly.get("ok"):
        print(weekly.get("error") or f"{args.index} 周线不可用，未生成报告。", file=sys.stderr)
        return 1
    as_of = (bundle.get("meta") or {}).get("as_of")
    if as_of:
        bundle = _clip_bundle_as_of(bundle, as_of)
    from output_paths import dp_cycle_dir, dp_cycle_store_dir

    payload = bundle_to_web_payload(bundle, index_label=label, rule_version=args.rule_version)
    if _read_share_events() != share_events:
        raise RuntimeError("ETF 份额事件表在报告生成期间发生变化，请重新生成报告")
    if HOLDER_DISCLOSURES_PATH.read_bytes() != holder_disclosures:
        raise RuntimeError("持有人披露表在报告生成期间发生变化，请重新生成报告")
    diagnosis = payload.get("diagnosis") or {}
    dated = Path(dp_cycle_dir())
    store = Path(dp_cycle_store_dir())
    run_dir = _new_run_dir(dated)
    _save_inputs(run_dir, bundle, share_events, holder_disclosures)
    (run_dir / "payload.json").write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    archived_web = run_dir / "web_snapshot"
    snap = write_web_snapshot(payload, index_symbol=args.index, output_dir=archived_web)
    if not Path(snap).is_file():
        raise RuntimeError("网页快照生成失败")

    pdf = None
    if args.pdf:
        from data_source.dp_cycle.pipeline import run_dp_cycle_pipeline

        result = run_dp_cycle_pipeline(
            index_symbol=args.index, refresh_db=False,
            bundle=bundle, diagnosis=diagnosis, out_dir=str(run_dir), publish_latest=False,
        )
        pdf = Path(result["pdf"])
        if not pdf.is_file():
            raise RuntimeError("PDF 生成失败")
    meta = bundle.get("meta") or {}
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "analysis_date": diagnosis.get("as_of") or meta.get("as_of"),
        "index_symbol": args.index,
        "rule_version": diagnosis.get("rule_version") or args.rule_version,
        "data_availability": diagnosis.get("data_availability") or {},
        "coverage": payload.get("coverage") or {},
        "coverage_span": payload.get("coverage_span") or {},
        "coverage_note": payload.get("coverage_note") or {},
        "quality": payload.get("quality") or {},
        "parameters": {"refresh": args.refresh, "pdf": args.pdf},
        "sources": {"trade_calendar": meta.get("trade_calendar"), "us_crowding": meta.get("us_crowding"),
                    "etf_flow": bundle.get("etf_flow"), "etf_holder_disclosures": bundle.get("etf_holder_disclosures")},
        "diagnosis": diagnosis,
        "artifacts": {"web": str(archived_web / "index.html"), "pdf": str(pdf) if pdf else None,
                      "payload": str(run_dir / "payload.json"), "inputs": str(run_dir / "inputs"),
                      "etf_share_events": str(run_dir / "inputs" / "etf_share_events.csv") if share_events is not None else None,
                      "etf_holder_disclosures": str(run_dir / "inputs" / "etf_holder_disclosures.csv"),
                      "latest_web": str(store / "web_snapshot" / "index.html"),
                      "latest_pdf": str(store / "大周期五项指标_长期跟踪.pdf") if pdf else None},
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    stable_web, latest_pdf = _publish(run_dir, archived_web, store, dated, pdf)
    print("web_snapshot:", stable_web)
    print("archive:", run_dir)
    if pdf:
        print("pdf:", dated / "大周期五项指标_长期跟踪.pdf")
    ingest = (bundle.get("meta") or {}).get("ingest") or {}
    if args.refresh:
        failed = [key for key, value in ingest.items() if isinstance(value, dict) and value.get("ok") is False]
        if not ingest.get("ok") and not failed:
            failed.append("数据入库")
        if (meta.get("trade_calendar") or {}).get("refresh_status") == "failed":
            failed.append("交易日历")
        crowding = meta.get("us_crowding") or {}
        if crowding.get("refresh_failed") is True or crowding.get("ok") is False or crowding.get("partial") is True:
            failed.append("美股拥挤度")
        if failed:
            print("更新未全部完成；已生成的报告可能沿用缓存：" + "、".join(failed), file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
