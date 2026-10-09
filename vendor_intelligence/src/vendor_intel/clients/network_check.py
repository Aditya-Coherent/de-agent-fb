"""Lightweight connectivity check before live search."""
from __future__ import annotations

import socket
import time

# Hosts used for preflight (any one succeeding = DNS OK for search)
_PROBE_HOSTS = ("www.bing.com", "www.google.com", "example.com")


def _resolve_host(host: str, timeout: float) -> bool:
    prev = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        return True
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(prev)


def check_internet_dns(
    timeout: float = 10.0,
    *,
    retries: int = 3,
) -> tuple[bool, str]:
    """
    Return (ok, message). Retries DNS — slow DNS (2s+ timeouts) should not
    mark the whole run offline after a single failure.
    """
    last_err = ""
    for attempt in range(retries):
        for host in _PROBE_HOSTS:
            if _resolve_host(host, timeout):
                return True, ""
        last_err = f"DNS resolution failed for {_PROBE_HOSTS} (attempt {attempt + 1}/{retries})"
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
    return False, last_err
