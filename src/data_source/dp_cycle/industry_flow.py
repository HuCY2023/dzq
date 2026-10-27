# -*- coding: utf-8 -*-
"""东财行业资金流日频。免费窗口与大盘相同，大约 120 个交易日，靠日更累积。"""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
import requests

_LIST = "https://push2.eastmoney.com/api/qt/clist/get"
_KLINE = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/bkzj/hy.html",
}
_PAGE_SIZE = 100

# 固定目标代码，避免按每日排名或同名的不同层级混拼。银行使用东财“银行Ⅱ”。
# 互联网服务为历史板块；现行目录缺失时不以 IT 服务等其他板块替代。
TECH_BOARD_CODES = {
    "半导体": "BK1036", "消费电子": "BK1037", "计算机设备": "BK0735",
    "软件开发": "BK0737", "通信设备": "BK0448", "光学光电子": "BK1038",
    "元件": "BK0459", "互联网服务": "BK0447",
}
BANK_BOARD_CODES = {"银行Ⅱ": "BK0475"}


def fetch_industry_flows() -> tuple[pd.DataFrame, dict[str, Any]]:
    meta: dict[str, Any] = {"source": "eastmoney.industry.fflow.daykline", "ok": False, "error": None}
    session = requests.Session()
    session.trust_env = False
    directory: dict[str, str] = {}
    page, total = 1, None
    while True:
        try:
            r = session.get(
                _LIST,
                params={
                    "pn": str(page), "pz": str(_PAGE_SIZE), "po": "1", "np": "1",
                    "fltt": "2", "invt": "2", "fid": "f12", "fs": "m:90+t:2",
                    "fields": "f12,f14",
                },
                headers=_HEADERS, timeout=30,
            )
            r.raise_for_status()
            data = r.json().get("data") or {}
            rows = data.get("diff") or []
            current_total = int(data.get("total") or 0)
            if total is None:
                total = current_total
            elif current_total != total:
                meta["error"] = "行业目录分页期间总数变化，目录不完整"
                break
            for row in rows:
                code = str(row.get("f12") or "").strip()
                name = str(row.get("f14") or "").strip()
                if code and name:
                    directory[code] = name
            if not rows or page * _PAGE_SIZE >= total:
                break
            page += 1
        except Exception as exc:  # noqa: BLE001
            meta["error"] = f"行业目录第 {page} 页: {type(exc).__name__}: {exc}"
            break

    meta["directory_pages"] = page if meta["error"] is None else page - 1
    meta["directory_total"] = total
    meta["directory_count"] = len(directory)
    directory_complete = bool(total and len(directory) == total and meta["error"] is None)
    targets = {**TECH_BOARD_CODES, **BANK_BOARD_CODES}
    boards = [(code, name) for name, code in targets.items() if directory.get(code) == name]
    meta["boards"] = len(boards)
    meta["missing_targets"] = [name for name, code in targets.items() if directory.get(code) != name]
    parts: list[pd.DataFrame] = []
    failed: list[str] = []
    for i, (code, name) in enumerate(boards):
        if i:
            time.sleep(0.15)
        try:
            r = session.get(
                _KLINE,
                params={
                    "lmt": "0",
                    "klt": "101",
                    "secid": f"90.{code}",
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54",
                },
                headers=_HEADERS,
                timeout=25,
            )
            r.raise_for_status()
            klines = ((r.json().get("data") or {}).get("klines")) or []
        except Exception:
            failed.append(name)
            continue
        rows = []
        for line in klines:
            p = str(line).split(",")
            if len(p) < 4:
                continue
            try:
                rows.append(
                    {
                        "date": p[0],
                        "bk_code": code,
                        "name": name,
                        "net_yi": float(p[1]) / 1e8,
                        "small_yi": float(p[2]) / 1e8,
                        "mid_yi": float(p[3]) / 1e8,
                    }
                )
            except (TypeError, ValueError):
                continue
        if rows:
            parts.append(pd.DataFrame(rows))
        else:
            failed.append(name)
    meta["directory_complete"] = directory_complete
    if not parts:
        meta.update({"failed": len(failed), "failed_targets": failed, "partial": True,
                     "latest_by_target": {name: None for name in targets}, "lagging_targets": []})
        meta["error"] = meta.get("error") or "no industry klines"
        return pd.DataFrame(), meta
    out = pd.concat(parts, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date", "net_yi"])
    out = out.drop_duplicates(["date", "bk_code"], keep="last")
    latest_by_code = out.groupby("bk_code")["date"].max().to_dict()
    for code, name in boards:
        if code not in latest_by_code and name not in failed:
            failed.append(name)
    latest = out["date"].max() if not out.empty else None
    meta["latest_by_target"] = {
        name: str(latest_by_code[code].date()) if code in latest_by_code else None
        for name, code in targets.items()
    }
    meta["lagging_targets"] = [
        name for name, code in targets.items()
        if code in latest_by_code and latest_by_code[code] < latest
    ]
    meta["failed"] = len(failed)
    meta["failed_targets"] = failed
    meta["partial"] = not directory_complete or bool(meta["missing_targets"] or failed or meta["lagging_targets"])
    if out.empty:
        meta["error"] = meta.get("error") or "no valid industry klines"
        return out.reset_index(drop=True), meta
    meta["ok"] = not meta["partial"]
    meta["rows"] = int(len(out))
    meta["first"] = str(out["date"].min().date())
    meta["last"] = str(out["date"].max().date())
    return out.reset_index(drop=True), meta
