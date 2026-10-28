"""Import an existing local turnover-concentration report as a partial observation."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from data_source.dp_cycle.turnover_observation import build_observation

OUTPUT_ROOT = ROOT / "output" / "user" / "dp_cycle" / "turnover_imports"


def run_import(source: Path, output_root: Path) -> dict:
    source = Path(source)
    summary_bytes = (source / "summary.json").read_bytes()
    top_bytes = (source / "latest-top5.csv").read_bytes()
    summary = json.loads(summary_bytes.decode("utf-8-sig"))
    top = pd.read_csv(io.BytesIO(top_bytes), dtype={"symbol": str})
    observation = build_observation(summary, top)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    snapshot = Path(tempfile.mkdtemp(prefix="turnover-", dir=output_root))
    (snapshot / "source_summary.json").write_bytes(summary_bytes)
    (snapshot / "latest-top5.csv").write_bytes(top_bytes)
    (snapshot / "observation.json").write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**observation, "snapshot": str(snapshot.resolve())}


def main() -> int:
    parser = argparse.ArgumentParser(description="导入已有本地成交集中度报告，保留部分覆盖标签。")
    parser.add_argument("--input-dir", required=True, type=Path, help="包含 summary.json 和 latest-top5.csv 的目录")
    args = parser.parse_args()
    try:
        row = run_import(args.input_dir, OUTPUT_ROOT)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, pd.errors.ParserError) as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 1
    print(f"实际数据日期：{row['date']}；本地股票样本：{row['stocks']} 只；状态：部分覆盖。")
    print("来源输入已保存；该观察不参与评分，也不代表完整全市场。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
