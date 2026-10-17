# -*- coding: utf-8 -*-
"""akshare 请求：绕过失效代理 + 有限次重试。"""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_SETUP = False


def ensure_http() -> None:
    global _SETUP
    if _SETUP:
        return
    try:
        from data_source.csi500.ak_http import install_no_system_proxy_patch, setup_akshare_http

        install_no_system_proxy_patch()
        setup_akshare_http()
    except Exception:
        pass
    _SETUP = True


def call_with_retry(fn: Callable[..., T], *args: Any, retries: int = 3, delay: float = 1.5, **kwargs: Any) -> T:
    ensure_http()
    last: Exception | None = None
    for i in range(max(1, retries)):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — 远端偶发断连
            last = exc
            if i + 1 >= retries:
                break
            time.sleep(delay * (i + 1))
    assert last is not None
    raise last
