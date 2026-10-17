# -*- coding: utf-8 -*-
"""Standalone akshare HTTP helpers (no system proxy)."""
from __future__ import annotations

import requests

_NO_PROXY = False
_ORIG = None


def install_no_system_proxy_patch() -> None:
    global _NO_PROXY, _ORIG
    if _NO_PROXY:
        return
    _ORIG = requests.Session.request

    def patched(self, method, url, **kwargs):
        self.trust_env = False
        kwargs.pop("trust_env", None)
        kwargs.setdefault("proxies", {"http": None, "https": None})
        return _ORIG(self, method, url, **kwargs)

    requests.Session.request = patched
    _NO_PROXY = True


def setup_akshare_http() -> None:
    install_no_system_proxy_patch()
