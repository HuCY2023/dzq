#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Update DuckDB (2015+)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def main() -> int:
    ap = argparse.ArgumentParser(description="dzq · 大周期入库")
    ap.add_argument("--mode", default="manual")
    ap.add_argument("--index", default="sh000001")
    ap.add_argument("--skip-high-low", action="store_true", default=True)
    ap.add_argument("--with-high-low", action="store_true", help="尝试广度（无湖时仅乐咕）")
    args = ap.parse_args()
    skip = not args.with_high_low

    from data_source.dp_cycle.ingest import update_dp_cycle_db
    from data_source.dp_cycle.store import HISTORY_START_STR, db_path, series_status, table_counts

    detail = update_dp_cycle_db(
        mode=args.mode,
        index_symbol=args.index,
        high_low_start=HISTORY_START_STR,
        skip_high_low=skip,
    )
    print("db:", db_path())
    print("counts:", table_counts())
    print(series_status().to_string(index=False))
    print("detail:", json.dumps(detail, ensure_ascii=False, default=str)[:2000])
    return 0 if detail.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
