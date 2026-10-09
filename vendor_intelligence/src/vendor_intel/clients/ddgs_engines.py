"""
ddgs (deedy5/ddgs) engine lists and client helpers.

Official backends: https://github.com/deedy5/ddgs#engines
Package docs: https://pypi.org/project/ddgs/
"""
from __future__ import annotations

import os
import warnings
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

T = TypeVar("T")

# text() — README engines table
TEXT_BACKENDS_OFFICIAL: frozenset[str] = frozenset(
    {
        "bing",
        "brave",
        "duckduckgo",
        "google",
        "grokipedia",
        "mojeek",
        "startpage",
        "yandex",
        "yahoo",
        "wikipedia",
    }
)

# news() — README engines table
NEWS_BACKENDS_OFFICIAL: frozenset[str] = frozenset({"bing", "duckduckgo", "yahoo"})

# books() only — invalid for text()
NON_TEXT_BACKENDS: frozenset[str] = frozenset({"annasarchive"})

# Often blocked (WinError 10061) or poor for company discovery
BLOCKED_TEXT_BACKENDS: frozenset[str] = frozenset(
    {
        "startpage",
        "annasarchive",
        "wikipedia",  # use search_router Wikipedia fallback instead
    }
)

# Default when DDGS_BACKENDS is empty / auto / all (never pass "auto" to ddgs — uses Startpage)
SAFE_TEXT_BACKENDS: tuple[str, ...] = (
    "duckduckgo",
    "bing",
    "brave",
    "mojeek",
    "google",
    "yahoo",
)

SAFE_NEWS_BACKENDS: tuple[str, ...] = ("bing", "duckduckgo", "yahoo")

_PROXY_KWARG_UNSET = object()

# TCP probes for preflight / host_reachability (engine name → host:443)
ENGINE_TCP_HOSTS: dict[str, tuple[str, int]] = {
    "bing": ("www.bing.com", 443),
    "brave": ("search.brave.com", 443),
    "google": ("www.google.com", 443),
    "yahoo": ("www.yahoo.com", 443),
    "mojeek": ("www.mojeek.com", 443),
    "duckduckgo": ("duckduckgo.com", 443),
    "startpage": ("www.startpage.com", 443),
    "yandex": ("yandex.com", 443),
}


def _parse_verify() -> bool | str:
    raw = (os.getenv("DDGS_VERIFY") or "true").strip()
    low = raw.lower()
    if low in ("0", "false", "no", "off"):
        return False
    if low in ("1", "true", "yes", "on"):
        return True
    return raw  # PEM path


def ddgs_timeout() -> int:
    try:
        return max(10, int(os.getenv("DDGS_TIMEOUT", "25")))
    except ValueError:
        return 25


def ddgs_proxy() -> str | None:
    """Explicit DDGS_PROXY / HTTPS_PROXY only (not the verified pool)."""
    for key in ("DDGS_PROXY", "HTTPS_PROXY", "https_proxy"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return None


def ddgs_client_kwargs(
    *,
    proxy: str | None | object = _PROXY_KWARG_UNSET,
) -> dict[str, Any]:
    """Kwargs for DDGS(proxy=..., timeout=..., verify=...). Pass proxy= explicitly when funneling."""
    if proxy is _PROXY_KWARG_UNSET:
        chosen: str | None = ddgs_proxy()
    else:
        chosen = proxy  # type: ignore[assignment]
    return {
        "proxy": chosen,
        "timeout": ddgs_timeout(),
        "verify": _parse_verify(),
    }


def _filter_backends(
    raw: str,
    *,
    allowed: frozenset[str],
    blocked: frozenset[str],
    default: tuple[str, ...],
) -> tuple[str, list[str], list[str]]:
    """Return (comma-separated param, ordered names, dropped blocked/invalid names)."""
    raw = (raw or "").strip().lower()
    dropped: list[str] = []

    if raw in ("", "auto", "all"):
        names = list(default)
        return ",".join(names), names, dropped

    names: list[str] = []
    for part in raw.split(","):
        name = part.strip().lower()
        if not name or name in names:
            continue
        if name not in allowed and name not in blocked:
            dropped.append(name)
            continue
        if name in blocked or name in NON_TEXT_BACKENDS:
            dropped.append(name)
            continue
        names.append(name)

    if not names:
        names = list(default)
        dropped.append("(empty-after-filter)")

    return ",".join(names), names, dropped


def normalize_text_backends(raw: str | None = None) -> tuple[str, list[str], list[str]]:
    """Build ddgs.text(..., backend=...) — never startpage / auto."""
    env = raw if raw is not None else os.getenv("DDGS_BACKENDS") or ""
    return _filter_backends(
        env,
        allowed=TEXT_BACKENDS_OFFICIAL,
        blocked=BLOCKED_TEXT_BACKENDS,
        default=SAFE_TEXT_BACKENDS,
    )


def normalize_news_backends(raw: str | None = None) -> tuple[str, list[str]]:
    """Build ddgs.news(..., backend=...) — official news engines only."""
    news_env = raw if raw is not None else os.getenv("DDGS_NEWS_BACKENDS") or ""
    if news_env.strip() and news_env.strip().lower() not in ("", "auto", "all"):
        param, names, _ = _filter_backends(
            news_env,
            allowed=NEWS_BACKENDS_OFFICIAL,
            blocked=frozenset(),
            default=SAFE_NEWS_BACKENDS,
        )
        return param, names

    text_raw = (os.getenv("DDGS_BACKENDS") or "").strip().lower()
    if text_raw and text_raw not in ("auto", "all"):
        _, text_names, _ = normalize_text_backends(text_raw)
        names = [n for n in text_names if n in NEWS_BACKENDS_OFFICIAL]
        if names:
            return ",".join(names), names

    names = list(SAFE_NEWS_BACKENDS)
    return ",".join(names), names


def run_with_ddgs(fn: Callable[[Any], T], *, label: str = "ddgs") -> T:
    """
    Run fn(ddgs) rotating through the verified proxy pool (search/scrape/news).
  """
    from vendor_intel.clients.duckduckgo import _load_ddgs
    from vendor_intel.clients.http_proxy import (
        ensure_outbound_proxies,
        mark_proxy_failed,
    )
    from vendor_intel.clients.proxy_pool import (
        get_proxy_funnel,
        resolve_ddgs_proxies_to_try,
    )

    DDGS = _load_ddgs()
    if DDGS is None:
        raise RuntimeError("ddgs not installed — pip install -U ddgs")

    ensure_outbound_proxies()
    last_exc: BaseException | None = None

    for proxy in resolve_ddgs_proxies_to_try():
        tag = proxy or "direct"
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                with DDGS(**ddgs_client_kwargs(proxy=proxy)) as ddgs:
                    return fn(ddgs)
        except Exception as exc:
            last_exc = exc
            if proxy:
                get_proxy_funnel().mark_failed(proxy)
                mark_proxy_failed(proxy)
            logger = __import__("logging").getLogger(__name__)
            logger.debug("%s failed via %s: %s", label, tag, exc)
            continue

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{label}: all proxy attempts failed")


@contextmanager
def open_ddgs() -> Iterator[Any]:
    """Single ddgs client using active proxy (prefer run_with_ddgs for retries)."""
    from vendor_intel.clients.duckduckgo import _load_ddgs
    from vendor_intel.clients.http_proxy import ensure_outbound_proxies

    ensure_outbound_proxies()
    DDGS = _load_ddgs()
    if DDGS is None:
        raise RuntimeError("ddgs not installed — pip install -U ddgs")
    with DDGS(**ddgs_client_kwargs()) as client:
        yield client
