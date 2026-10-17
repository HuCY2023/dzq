# -*- coding: utf-8 -*-
"""Path helpers for standalone dzq (replaces ftqtz output_paths)."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def project_root(start: str | None = None) -> str:
    del start
    return str(_ROOT)


def output_root(start: str | None = None) -> str:
    p = os.path.join(project_root(start), "output")
    os.makedirs(p, exist_ok=True)
    return p


def dated_output_dir(for_date: date | None = None, start: str | None = None) -> str:
    d = for_date or date.today()
    p = os.path.join(output_root(start), d.isoformat())
    os.makedirs(p, exist_ok=True)
    return p


def dp_cycle_dir(for_date: date | None = None, start: str | None = None) -> str:
    p = os.path.join(dated_output_dir(for_date, start), "dp_cycle")
    os.makedirs(p, exist_ok=True)
    return p


def dp_cycle_store_dir(start: str | None = None) -> str:
    p = os.path.join(output_root(start), "user", "dp_cycle")
    os.makedirs(p, exist_ok=True)
    return p
