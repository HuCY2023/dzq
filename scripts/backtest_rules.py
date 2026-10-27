#!/usr/bin/env python3
"""Run a small evidence-gated allocation replay or report missing prerequisites."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from data_source.dp_cycle.evaluation import DEFAULT_ONE_WAY_COST, evaluate_allocation, evaluate_warning_windows, purged_time_split


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="仅在时点、交易价格和总收益复权凭证齐全时回放配置。")
    parser.add_argument("--prices", type=Path, help="含交易日、真实开盘、总收益复权开盘及可成交状态的 CSV")
    parser.add_argument("--signals", type=Path, help="含信号生成时间、数据可得时间和目标仓位的 CSV；缺失目标会撤销待执行指令")
    parser.add_argument("--warnings", type=Path, help="可选：含预警生成时间、数据可得时间和 warning 状态的 CSV")
    parser.add_argument("--calendar", type=Path, help="与价格区间一致、含 date 列的交易日历 CSV")
    parser.add_argument("--evidence", type=Path, help="标的、来源、复权/分红及历史版本证据 JSON")
    parser.add_argument("--output", type=Path, help="可选：在该目录创建独立运行记录")
    parser.add_argument("--one-way-cost", type=float, default=DEFAULT_ONE_WAY_COST,
                        help="单边成本比例；默认 0.0005，敏感性检查可用 0.001")
    parser.add_argument("--train-end", help="预先指定训练集截止日期")
    parser.add_argument("--validation-end", help="预先指定验证集截止日期")
    parser.add_argument("--test-end", help="测试集截止日期，默认日历最后一天")
    parser.add_argument("--label-horizon", type=int, default=60, help="未来标签的交易日间隔，默认 60")
    args = parser.parse_args(argv)
    if not all((args.prices, args.signals, args.calendar, args.evidence)):
        result = {"status": "BLOCK", "reasons": [
            "需同时提供可交易价格、已知时点信号、交易日历和复权/分红/版本证据。",
            "现有本机行情复权和历史公告版本未完成验证，不能计算真实收益。",
        ]}
    else:
        try:
            prices = pd.read_csv(args.prices)
            signals = pd.read_csv(args.signals)
            calendar = pd.read_csv(args.calendar)
            evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
            if "date" not in calendar.columns or not isinstance(evidence, dict):
                raise ValueError("日历缺 date 列或证据不是 JSON 对象")
            result = evaluate_allocation(prices, signals, evidence, trading_dates=calendar["date"],
                                         one_way_cost=args.one_way_cost)
            if args.warnings and result["status"] != "BLOCK":
                warnings = pd.read_csv(args.warnings)
                warning_windows = evaluate_warning_windows(prices, warnings, evidence, trading_dates=calendar["date"])
                result["warning_windows"] = warning_windows
                if warning_windows["status"] != "OK":
                    result["status"] = "DATA_PARTIAL"
                result["limitations"].append("预警仅计算全输入区间内不重叠的开盘观察窗口，尚未按分区统计或定义误报、漏报。")
            if any((args.train_end, args.validation_end, args.test_end)) and result["status"] != "BLOCK":
                if not args.train_end or not args.validation_end:
                    raise ValueError("时间分区需要同时提供训练和验证截止日")
                split = purged_time_split(calendar["date"], train_end=args.train_end,
                                          validation_end=args.validation_end, test_end=args.test_end,
                                          label_horizon=args.label_horizon)
                result["split"] = split
                result["limitations"].append("时间分区仅记录交易日候选边界，不代表已有信号；净值指标仍为全区间，尚未完成分区外评价。")
                if split["status"] != "OK":
                    result["status"] = "DATA_PARTIAL"
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            result = {"status": "BLOCK", "reasons": [f"输入无法核验：{exc}"]}
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="backtest-", dir=args.output)) / "evaluation.json"
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        print(f"评估记录：{output}")
    print(f"状态：{result['status']}")
    if result["status"] == "OK":
        print("计算已完成；验证协议尚未完成，不能据此认定规则有效。")
    elif result["status"] == "DATA_PARTIAL":
        print("部分输入或判断不完整；结果仅供检查，不能视为完整规则评价。")
    for reason in (result.get("warning_windows") or {}).get("reasons", []):
        print(f"预警范围：{reason}")
    for reason in result.get("reasons", []):
        print(f"原因：{reason}")
    for limitation in result.get("limitations", []):
        print(f"范围：{limitation}")
    return 0 if result["status"] == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
