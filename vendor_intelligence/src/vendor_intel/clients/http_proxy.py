"""
Use verified proxies for all outbound HTTP and ddgs (search, scrape, news, fallbacks).

Enable in .env:
  USE_PROXY_POOL=true          # master switch (or DDGS_USE_PROXY_POOL)
  DDGS_USE_PROXY_POOL=true
Run once:  python scripts/check_proxies.py
"""
from __future__ import annotations

import os
from typing import Any, Callable, TypeVar

import httpx

from vendor_intel.clients.proxy_pool import (
    ensure_proxy_pool_ready,
    get_proxy_funnel,
    proxy_pool_enabled,
    resolve_ddgs_proxies_to_try,
)

T = TypeVar("T")

_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "DDGS_PROXY",
    "ALL_PROXY",
)


def ensure_outbound_proxies(
    *,
    log_print: Callable[[str], None] | None = None,
) -> bool:
    """Load cache / auto-refresh when pool is enabled."""
    if not proxy_pool_enabled():
        return True
    ok = ensure_proxy_pool_ready(log_print=log_print)
    if ok:
        apply_active_proxy_to_env()
    return ok


def apply_active_proxy_to_env(proxy_url: str | None = None) -> str | None:
    """
    Set process-wide proxy env vars so httpx trust_env and ddgs share one proxy.
    """
    if not proxy_pool_enabled():
        return None

    if proxy_url is None:
        for candidate in resolve_ddgs_proxies_to_try():
            if candidate:
                proxy_url = candidate
                break

    if proxy_url:
        for key in _PROXY_ENV_KEYS:
            os.environ[key] = proxy_url
    else:
        for key in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)
    return proxy_url


def active_proxy_url() -> str | None:
    if not proxy_pool_enabled():
        explicit = (os.getenv("DDGS_PROXY") or os.getenv("HTTPS_PROXY") or "").strip()
        return explicit or None
    ensure_outbound_proxies()
    return (os.getenv("HTTPS_PROXY") or os.getenv("DDGS_PROXY") or "").strip() or None


def mark_proxy_failed(proxy_url: str | None) -> str | None:
    """Rotate to next proxy and update env."""
    if proxy_url:
        get_proxy_funnel().mark_failed(proxy_url)
    return apply_active_proxy_to_env()


def httpx_client_kwargs(*, skip_pool: bool = False, **extra: Any) -> dict[str, Any]:
    """Kwargs for httpx.Client / AsyncClient — includes proxy when pool enabled."""
    kw: dict[str, Any] = {
        "follow_redirects": True,
        "trust_env": True,
    }
    if not skip_pool and proxy_pool_enabled():
        ensure_outbound_proxies()
        proxy = active_proxy_url()
        if proxy:
            kw["proxy"] = proxy
    kw.update(extra)
    return kw


def httpx_client(*, skip_pool: bool = False, **extra: Any) -> httpx.Client:
    return httpx.Client(**httpx_client_kwargs(skip_pool=skip_pool, **extra))


def httpx_async_client(*, skip_pool: bool = False, **extra: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(**httpx_client_kwargs(skip_pool=skip_pool, **extra))
