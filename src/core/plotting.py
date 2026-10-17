# -*- coding: utf-8 -*-
"""Minimal Chinese font setup for matplotlib PDF (no talib/mplfinance)."""
from __future__ import annotations

import os
import platform

_DONE = False


def setup_matplotlib_chinese() -> bool:
    global _DONE
    if _DONE:
        return True
    try:
        import matplotlib
        from matplotlib import font_manager
        import matplotlib.pyplot as plt
    except Exception:
        return False

    candidates = []
    if platform.system() == "Windows":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        fonts = os.path.join(windir, "Fonts")
        candidates = [
            os.path.join(fonts, "msyh.ttc"),
            os.path.join(fonts, "simhei.ttf"),
            os.path.join(fonts, "simsun.ttc"),
        ]
    else:
        candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ]
    family = None
    for path in candidates:
        if os.path.isfile(path):
            try:
                font_manager.fontManager.addfont(path)
                family = font_manager.FontProperties(fname=path).get_name()
                break
            except Exception:
                continue
    if family:
        matplotlib.rcParams["font.sans-serif"] = [family, "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    _DONE = True
    return True
