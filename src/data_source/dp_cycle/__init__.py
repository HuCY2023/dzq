"""大周期五项指标取数与复现报告。

包初始化保持轻量：GUI 会 `from data_source.dp_cycle.gui_bundle / store import ...`，
切勿顶层拉 pipeline/report（matplotlib）或 ingest（网络库）。
"""

from __future__ import annotations

__all__ = [
    "HISTORY_START_STR",
    "SERIES_GRAIN",
    "db_path",
    "run_dp_cycle_pipeline",
    "update_dp_cycle_db",
]


def __getattr__(name: str):
    if name in ("HISTORY_START_STR", "SERIES_GRAIN", "db_path"):
        from data_source.dp_cycle import store as _store

        return getattr(_store, name)
    if name == "run_dp_cycle_pipeline":
        from data_source.dp_cycle.pipeline import run_dp_cycle_pipeline as _fn

        return _fn
    if name == "update_dp_cycle_db":
        from data_source.dp_cycle.ingest import update_dp_cycle_db as _fn

        return _fn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
