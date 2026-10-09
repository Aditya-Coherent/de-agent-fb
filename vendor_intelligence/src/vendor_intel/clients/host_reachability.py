"""Probe which ddgs engine hosts are reachable (TCP)."""
from __future__ import annotations

import socket
from functools import lru_cache

from vendor_intel.clients.ddgs_engines import ENGINE_TCP_HOSTS

# Hosts for ddgs text backends (see https://github.com/deedy5/ddgs#engines)
BACKEND_HOSTS: dict[str, tuple[str, int]] = dict(ENGINE_TCP_HOSTS)

SEARXNG_LOCAL = ("127.0.0.1", 8080)


def _tcp_ok(host: str, port: int, timeout: float = 4.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@lru_cache(maxsize=1)
def probe_search_hosts() -> dict[str, bool]:
    out: dict[str, bool] = {}
    for name, (host, port) in BACKEND_HOSTS.items():
        out[name] = _tcp_ok(host, port)
    out["searxng_local"] = _tcp_ok(*SEARXNG_LOCAL)
    return out


def searxng_local_reachable() -> bool:
    return probe_search_hosts().get("searxng_local", False)


@lru_cache(maxsize=1)
def searxng_search_usable() -> bool:
    """True when SearXNG returns at least one HTML/JSON result (not just TCP on 8080)."""
    if not searxng_local_reachable():
        return False
    try:
        from vendor_intel.clients.browser_headers import browser_headers
        from vendor_intel.clients.http_proxy import httpx_client
        from vendor_intel.clients.searxng import _parse_html_results, searxng_urls
        from vendor_intel.config import Settings

        base = searxng_urls(Settings.load().searxng_base_url)[0]
        hdrs = browser_headers(referer=base + "/")
        with httpx_client(timeout=12.0, headers=hdrs) as client:
            r = client.post(
                f"{base}/search",
                data={"q": "pharmaceutical companies", "category_general": "1"},
            )
            if r.status_code != 200:
                return False
            return len(_parse_html_results(r.text, 3)) > 0
    except Exception:
        return False


def print_connectivity_report() -> None:
    hosts = probe_search_hosts()
    print("  [search] ddgs engine hosts (TCP):", flush=True)
    for name in ("bing", "brave", "mojeek", "google", "yahoo"):
        if name in hosts:
            print(f"    {name}: {'OK' if hosts.get(name) else 'BLOCKED'}", flush=True)
    searx_tcp = hosts.get("searxng_local")
    searx_ok = searx_tcp and searxng_search_usable()
    if searx_ok:
        searx_label = "OK (returns results)"
    elif searx_tcp:
        searx_label = "UP but 0 hits (restart: docker compose down && docker compose up -d)"
    else:
        searx_label = "not running (docker compose up -d)"
    print(f"    searxng_local: {searx_label}", flush=True)
    if not searx_tcp:
        print(
            "  [search] Tip: run `docker compose up -d` for SearXNG fallback volume.",
            flush=True,
        )
