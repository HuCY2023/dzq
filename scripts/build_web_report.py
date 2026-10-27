#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build offline web_snapshot (LWC HTML) + optional PDF."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def main() -> int:
    ap = argparse.ArgumentParser(description="dzq · 网页/PDF 报告")
    ap.add_argument("--index", default="sh000001")
    ap.add_argument("--refresh", action="store_true", help="先更新入库再出报告")
    ap.add_argument("--pdf", action="store_true", help="同时生成长期跟踪 PDF")
    args = ap.parse_args()

    from data_source.dp_cycle.gui_bundle import (
        INDEX_CHOICES,
        bundle_to_web_payload,
        load_dp_cycle_gui_bundle,
        write_web_snapshot,
    )

    label = next((k for k, v in INDEX_CHOICES.items() if v == args.index), args.index)
    bundle = load_dp_cycle_gui_bundle(
        index_symbol=args.index,
        refresh=args.refresh,
        skip_high_low=True,
    )
    weekly = (bundle.get("meta") or {}).get("weekly") or {}
    if not weekly.get("ok"):
        print(weekly.get("error") or f"{args.index} 周线不可用，未生成报告。", file=sys.stderr)
        return 1
    payload = bundle_to_web_payload(bundle, index_label=label)
    snap = write_web_snapshot(payload, index_symbol=args.index)
    print("web_snapshot:", snap)

    if args.pdf:
        from data_source.dp_cycle.pipeline import run_dp_cycle_pipeline

        result = run_dp_cycle_pipeline(
            index_symbol=args.index, refresh_db=False,
            bundle=bundle, diagnosis=payload.get("diagnosis"),
        )
        print("pdf:", result["pdf"])
    ingest = (bundle.get("meta") or {}).get("ingest") or {}
    if args.refresh and not ingest.get("ok"):
        failed = [key for key, value in ingest.items() if isinstance(value, dict) and value.get("ok") is False]
        print("更新未全部完成；已生成的报告可能沿用缓存：" + "、".join(failed), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
