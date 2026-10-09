"""
Fetch free proxies, verify HTTPS, cache working ones, funnel into ddgs.

Sources:
  - https://github.com/proxifly/free-proxy-list (jsDelivr CDN)
  - https://api.proxyscrape.com/v4/free-proxy-list/get?...
  - https://proxylist.geonode.com/api/proxy-list?...
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import httpx

logger = logging.getLogger(__name__)

PROXYSCRAPE_URL = (
    "https://api.proxyscrape.com/v4/free-proxy-list/get"
    "?request=display_proxies&proxy_format=protocolipport&format=text"
)
GEONODE_URL = (
    "https://proxylist.geonode.com/api/proxy-list"
    "?limit={limit}&page={page}&sort_by=lastChecked&sort_type=desc"
)
# https://github.com/proxifly/free-proxy-list
PROXIFLY_HTTP_JSON = (
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/"
    "proxies/protocols/http/data.json"
)
PROXIFLY_HTTPS_JSON = (
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/"
    "proxies/protocols/https/data.json"
)
PROXIFLY_SOCKS5_JSON = (
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/"
    "proxies/protocols/socks5/data.json"
)

DEFAULT_TEST_URL = "https://www.bing.com/"
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

_ALLOWED_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})


@dataclass(frozen=True)
class ProxyEntry:
    url: str
    source: str
    latency_ms: float | None = None
    ddgs_ok: bool = False

    def __str__(self) -> str:
        return self.url


@dataclass
class ProxyPoolCache:
    updated_at: str = ""
    verified: list[dict] = field(default_factory=list)

    def to_entries(self) -> list[ProxyEntry]:
        out: list[ProxyEntry] = []
        for row in self.verified:
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            out.append(
                ProxyEntry(
                    url=url,
                    source=str(row.get("source") or "cache"),
                    latency_ms=row.get("latency_ms"),
                    ddgs_ok=bool(row.get("ddgs_ok")),
                )
            )
        return out


def default_cache_path() -> Path:
    raw = (os.getenv("PROXY_POOL_CACHE") or "output/proxy_pool_verified.json").strip()
    return Path(raw)


def _fetch_timeout() -> float:
    try:
        return max(5.0, float(os.getenv("PROXY_FETCH_TIMEOUT", "30")))
    except ValueError:
        return 30.0


def _check_timeout() -> float:
    try:
        return max(3.0, float(os.getenv("PROXY_CHECK_TIMEOUT", "12")))
    except ValueError:
        return 12.0


def _cache_max_age_sec() -> int:
    try:
        return max(300, int(os.getenv("PROXY_CACHE_MAX_AGE_SEC", "3600")))
    except ValueError:
        return 3600


def _env_true(name: str, default: str = "false") -> bool:
    return (os.getenv(name) or default).strip().lower() in ("1", "true", "yes", "on")


def proxy_pool_enabled() -> bool:
    """Master switch for routing all outbound traffic through verified proxies."""
    return _env_true("USE_PROXY_POOL") or _env_true("DDGS_USE_PROXY_POOL")


def _normalize_proxy_url(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"http://{raw}"
    scheme = raw.split("://", 1)[0].lower()
    if scheme == "socks5":
        raw = "socks5h://" + raw.split("://", 1)[1]
        scheme = "socks5h"
    if scheme not in _ALLOWED_SCHEMES:
        return None
    return raw


def fetch_proxyscrape(
    *,
    client: httpx.Client | None = None,
    max_lines: int = 800,
) -> list[ProxyEntry]:
    own = client is None
    if own:
        client = httpx.Client(
            timeout=_fetch_timeout(),
            follow_redirects=True,
            headers=_FETCH_HEADERS,
        )
    try:
        assert client is not None
        r = client.get(PROXYSCRAPE_URL)
        r.raise_for_status()
        out: list[ProxyEntry] = []
        seen: set[str] = set()
        for line in r.text.splitlines():
            url = _normalize_proxy_url(line)
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(ProxyEntry(url=url, source="proxyscrape"))
            if len(out) >= max_lines:
                break
        return out
    finally:
        if own and client is not None:
            client.close()


def fetch_geonode(
    *,
    client: httpx.Client | None = None,
    limit: int = 500,
    page: int = 1,
) -> list[ProxyEntry]:
    own = client is None
    if own:
        client = httpx.Client(
            timeout=_fetch_timeout(),
            follow_redirects=True,
            headers=_FETCH_HEADERS,
        )
    try:
        assert client is not None
        url = GEONODE_URL.format(limit=limit, page=page)
        r = client.get(url)
        r.raise_for_status()
        rows = r.json().get("data") or []
        out: list[ProxyEntry] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            ip = str(row.get("ip") or "").strip()
            port = str(row.get("port") or "").strip()
            if not ip or not port:
                continue
            protocols = row.get("protocols") or []
            if not isinstance(protocols, list):
                protocols = [str(protocols)]
            latency = row.get("latency")
            lat_ms = float(latency) if latency is not None else None
            for proto in protocols:
                scheme = str(proto).strip().lower()
                if scheme == "socks4":
                    continue
                if scheme == "socks5":
                    scheme = "socks5h"
                if scheme not in _ALLOWED_SCHEMES:
                    continue
                norm = _normalize_proxy_url(f"{scheme}://{ip}:{port}")
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                out.append(ProxyEntry(url=norm, source="geonode", latency_ms=lat_ms))
                break
        return out
    finally:
        if own and client is not None:
            client.close()


def _parse_proxifly_rows(rows: list) -> list[ProxyEntry]:
    out: list[ProxyEntry] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _normalize_proxy_url(str(row.get("proxy") or ""))
        if not url and row.get("ip") and row.get("port"):
            proto = str(row.get("protocol") or "http").lower()
            if proto == "socks5":
                proto = "socks5h"
            url = _normalize_proxy_url(f"{proto}://{row['ip']}:{row['port']}")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(ProxyEntry(url=url, source="proxifly"))
    return out


def fetch_proxifly(
    *,
    client: httpx.Client | None = None,
    include_socks5: bool = True,
    max_per_feed: int = 600,
) -> list[ProxyEntry]:
    """Proxifly free-proxy-list via jsDelivr (http + https + optional socks5)."""
    own = client is None
    if own:
        client = httpx.Client(
            timeout=_fetch_timeout(),
            follow_redirects=True,
            headers=_FETCH_HEADERS,
        )
    feeds = [PROXIFLY_HTTPS_JSON, PROXIFLY_HTTP_JSON]
    if include_socks5:
        feeds.append(PROXIFLY_SOCKS5_JSON)

    out: list[ProxyEntry] = []
    seen: set[str] = set()
    try:
        assert client is not None
        for feed_url in feeds:
            try:
                r = client.get(feed_url)
                r.raise_for_status()
                rows = r.json()
                if not isinstance(rows, list):
                    continue
                for entry in _parse_proxifly_rows(rows[:max_per_feed]):
                    if entry.url in seen:
                        continue
                    seen.add(entry.url)
                    out.append(entry)
            except Exception as exc:
                logger.debug("proxifly feed %s failed: %s", feed_url, exc)
        return out
    finally:
        if own and client is not None:
            client.close()


def merge_proxy_lists(
    *lists: Iterable[ProxyEntry],
    shuffle: bool = True,
    cap: int | None = None,
) -> list[ProxyEntry]:
    merged: list[ProxyEntry] = []
    seen: set[str] = set()
    for lst in lists:
        for entry in lst:
            if entry.url in seen:
                continue
            seen.add(entry.url)
            merged.append(entry)
    if shuffle:
        random.shuffle(merged)
    if cap is not None and cap > 0:
        merged = merged[:cap]
    return merged


def check_proxy(
    proxy_url: str,
    *,
    test_url: str | None = None,
    timeout: float | None = None,
) -> tuple[bool, str, float | None]:
    test_url = test_url or os.getenv("PROXY_TEST_URL", DEFAULT_TEST_URL)
    timeout = timeout if timeout is not None else _check_timeout()
    proxy_url = _normalize_proxy_url(proxy_url) or proxy_url

    t0 = time.perf_counter()
    try:
        with httpx.Client(
            proxy=proxy_url,
            timeout=timeout,
            follow_redirects=True,
            headers=_FETCH_HEADERS,
            verify=True,
        ) as client:
            r = client.get(test_url)
            elapsed = (time.perf_counter() - t0) * 1000.0
            if r.status_code < 500:
                return True, f"HTTP {r.status_code}", elapsed
            return False, f"HTTP {r.status_code}", elapsed
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return False, f"{type(exc).__name__}: {exc}"[:120], elapsed


def find_working_proxies(
    entries: list[ProxyEntry],
    *,
    max_check: int = 40,
    max_workers: int = 12,
    test_url: str | None = None,
    on_progress: Callable[[ProxyEntry, bool, str], None] | None = None,
) -> list[ProxyEntry]:
    if not entries:
        return []

    try:
        max_check = max(1, int(os.getenv("PROXY_MAX_CHECK", str(max_check))))
    except ValueError:
        max_check = 40
    try:
        max_workers = max(1, int(os.getenv("PROXY_CHECK_WORKERS", str(max_workers))))
    except ValueError:
        max_workers = 12

    to_test = entries[:max_check]
    working: list[tuple[ProxyEntry, float]] = []

    def _probe(entry: ProxyEntry) -> tuple[ProxyEntry, bool, str, float | None]:
        ok, detail, ms = check_proxy(entry.url, test_url=test_url)
        if on_progress:
            on_progress(entry, ok, detail)
        return entry, ok, detail, ms

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_probe, e) for e in to_test]
        for fut in as_completed(futures):
            entry, ok, _detail, ms = fut.result()
            if ok and ms is not None:
                working.append((entry, ms))

    working.sort(key=lambda x: x[1])
    result: list[ProxyEntry] = []
    for entry, ms in working:
        result.append(
            ProxyEntry(
                url=entry.url,
                source=entry.source,
                latency_ms=ms,
                ddgs_ok=entry.ddgs_ok,
            )
        )
    return result


def check_ddgs_via_proxy(
    proxy_url: str,
    query: str | None = None,
    *,
    backend: str | None = None,
) -> tuple[bool, str]:
    import warnings

    from vendor_intel.clients.duckduckgo import _load_ddgs

    query = query or os.getenv(
        "PROXY_DDGS_TEST_QUERY", "Sun Pharmaceutical Industries India"
    )
    backend = backend or (os.getenv("PROXY_DDGS_TEST_BACKEND") or "bing").strip()

    DDGS = _load_ddgs()
    if DDGS is None:
        return False, "ddgs not installed"

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with DDGS(proxy=proxy_url, timeout=25) as ddgs:
                items = list(
                    ddgs.text(
                        query,
                        region="in-en",
                        max_results=1,
                        backend=backend,
                        safesearch="moderate",
                    )
                )
        if items:
            return True, f"{len(items)} hit(s)"
        return False, "0 results"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:100]


def fetch_all_proxy_sources(
    *,
    geonode_limit: int = 500,
    proxyscrape_max: int = 800,
    proxifly_max: int = 600,
) -> tuple[list[ProxyEntry], dict[str, str]]:
    status: dict[str, str] = {}
    lists: list[list[ProxyEntry]] = []

    with httpx.Client(
        timeout=_fetch_timeout(),
        follow_redirects=True,
        headers=_FETCH_HEADERS,
    ) as client:
        for name, fn in (
            ("proxifly", lambda: fetch_proxifly(client=client, max_per_feed=proxifly_max)),
            (
                "proxyscrape",
                lambda: fetch_proxyscrape(client=client, max_lines=proxyscrape_max),
            ),
            ("geonode", lambda: fetch_geonode(client=client, limit=geonode_limit)),
        ):
            try:
                batch = fn()
                lists.append(batch)
                status[name] = f"{len(batch)} proxies"
            except Exception as exc:
                status[name] = f"FAIL: {exc}"

    return merge_proxy_lists(*lists, shuffle=True), status


def save_verified_pool(
    entries: list[ProxyEntry],
    path: Path | None = None,
) -> Path:
    path = path or default_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = ProxyPoolCache(
        updated_at=datetime.now(timezone.utc).isoformat(),
        verified=[
            {
                "url": e.url,
                "source": e.source,
                "latency_ms": e.latency_ms,
                "ddgs_ok": e.ddgs_ok,
            }
            for e in entries
        ],
    )
    path.write_text(json.dumps(asdict(payload), indent=2), encoding="utf-8")
    return path


def load_verified_pool(path: Path | None = None) -> list[ProxyEntry]:
    path = path or default_cache_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        cache = ProxyPoolCache(
            updated_at=str(raw.get("updated_at") or ""),
            verified=list(raw.get("verified") or []),
        )
        if cache.updated_at:
            updated = datetime.fromisoformat(cache.updated_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - updated).total_seconds()
            if age > _cache_max_age_sec():
                return []
        return cache.to_entries()
    except Exception as exc:
        logger.debug("load_verified_pool failed: %s", exc)
        return []


@dataclass
class ProxyPipelineResult:
    """Summary of fetch → verify → cache."""

    fetched_total: int = 0
    sources: dict[str, str] = field(default_factory=dict)
    https_verified: int = 0
    ddgs_verified: int = 0
    cache_path: Path = field(default_factory=default_cache_path)
    entries: list[ProxyEntry] = field(default_factory=list)

    @property
    def best(self) -> ProxyEntry | None:
        for e in self.entries:
            if e.ddgs_ok:
                return e
        return self.entries[0] if self.entries else None


def run_proxy_pipeline(
    *,
    max_check: int = 40,
    test_ddgs: bool = True,
    max_ddgs_test: int = 8,
    cache_path: Path | None = None,
    apply_to_env: bool = True,
    log_print: Callable[[str], None] | None = None,
) -> ProxyPipelineResult:
    """
    Streamlined flow:
      1. Fetch — Proxifly, ProxyScrape, Geonode
      2. Verify — HTTPS probe, then ddgs.text on fastest
      3. Utilize — save cache + set DDGS_PROXY to best ddgs-verified proxy
    """
    _log = log_print or (lambda msg: logger.info("[proxy] %s", msg))
    result = ProxyPipelineResult(cache_path=cache_path or default_cache_path())

    _log("Step 1/3 — Fetch proxy lists")
    merged, status = fetch_all_proxy_sources()
    result.sources = status
    result.fetched_total = len(merged)
    for src, msg in status.items():
        _log(f"  {src}: {msg}")
    if not merged:
        _log("No proxies downloaded.")
        return result

    _log(f"Step 2/3 — Verify HTTPS (up to {max_check} proxies)")
    working = find_working_proxies(
        merged,
        max_check=max_check,
        on_progress=lambda e, ok, d: _log(
            f"  [{'OK' if ok else 'fail'}] {e.url[:50]} — {d}"
        )
        if log_print
        else None,
    )
    result.https_verified = len(working)
    if not working:
        _log("No proxies passed HTTPS check.")
        return result

    verified: list[ProxyEntry] = []
    if test_ddgs:
        _log(f"Step 3/3 — Verify ddgs + save (top {min(max_ddgs_test, len(working))})")
        for entry in working[:max_ddgs_test]:
            ok, detail = check_ddgs_via_proxy(entry.url)
            _log(f"  ddgs {'OK' if ok else 'fail'}: {entry.url[:48]} — {detail}")
            if ok:
                verified.append(
                    ProxyEntry(
                        url=entry.url,
                        source=entry.source,
                        latency_ms=entry.latency_ms,
                        ddgs_ok=True,
                    )
                )
        if not verified:
            _log("No ddgs hits; caching fastest HTTPS-only proxies.")
            verified = working[:10]
    else:
        _log("Step 3/3 — Save HTTPS-verified pool")
        verified = working

    result.ddgs_verified = sum(1 for e in verified if e.ddgs_ok)
    result.entries = verified
    result.cache_path = save_verified_pool(verified, result.cache_path)
    _log(f"Cached {len(verified)} proxies → {result.cache_path}")

    if apply_to_env and verified:
        best = result.best
        if best:
            get_proxy_funnel().reload()
            from vendor_intel.clients.http_proxy import apply_active_proxy_to_env

            apply_active_proxy_to_env(best.url)
            _log(f"Active proxy for all HTTP/ddgs: {best.url}")

    return result


def ensure_proxy_pool_ready(
    *,
    log_print: Callable[[str], None] | None = None,
) -> bool:
    """
    Before live search: load cache or run full pipeline if DDGS_AUTO_PROXY=true.
    Returns True when at least one proxy is available (or pool disabled).
    """
    if not proxy_pool_enabled():
        return True

    entries = load_verified_pool()
    if entries:
        get_proxy_funnel().reload()
        return True

    if not _env_true("DDGS_AUTO_PROXY"):
        if log_print:
            log_print(
                "Proxy pool empty — run: .venv\\Scripts\\python.exe scripts\\check_proxies.py"
            )
        return False

    result = run_proxy_pipeline(
        max_check=int(os.getenv("PROXY_MAX_CHECK", "30")),
        test_ddgs=_env_true("PROXY_TEST_DDGS", "true"),
        log_print=log_print,
    )
    return bool(result.entries)


def refresh_verified_pool(
    *,
    max_check: int = 40,
    test_ddgs: bool = True,
    max_ddgs_test: int = 8,
    log_print: Callable[[str], None] | None = None,
    cache_path: Path | None = None,
) -> list[ProxyEntry]:
    """Fetch all sources → HTTPS probe → optional ddgs test → save cache."""
    _log = log_print or (lambda msg: logger.info("%s", msg))

    _log("Fetching proxies: Proxifly + ProxyScrape + Geonode…")
    merged, status = fetch_all_proxy_sources()
    for src, msg in status.items():
        _log(f"  {src}: {msg}")
    if not merged:
        return []

    _log(f"Probing up to {max_check} proxies (HTTPS → {DEFAULT_TEST_URL})…")

    def _progress(entry: ProxyEntry, ok: bool, detail: str) -> None:
        mark = "OK" if ok else "fail"
        _log(f"  [{mark}] {entry.url[:55]} ({entry.source}) — {detail}")

    working = find_working_proxies(merged, max_check=max_check, on_progress=_progress)
    if not working:
        _log("No proxies passed HTTPS check.")
        return []

    verified: list[ProxyEntry] = []
    if test_ddgs:
        _log(f"Testing top {min(max_ddgs_test, len(working))} with ddgs.text()…")
        for entry in working[:max_ddgs_test]:
            ok, detail = check_ddgs_via_proxy(entry.url)
            _log(
                f"  ddgs {'OK' if ok else 'fail'}: {entry.url[:50]} — {detail}"
            )
            if ok:
                verified.append(
                    ProxyEntry(
                        url=entry.url,
                        source=entry.source,
                        latency_ms=entry.latency_ms,
                        ddgs_ok=True,
                    )
                )
        if not verified:
            _log("No ddgs-verified proxies; saving HTTPS-only pool.")
            verified = working[:10]
    else:
        verified = working

    path = save_verified_pool(verified, cache_path)
    _log(f"Saved {len(verified)} proxies → {path}")
    return verified


class ProxyFunnel:
    """Rotate verified proxies into ddgs when direct search fails."""

    def __init__(self) -> None:
        self._queue: deque[ProxyEntry] = deque()
        self._failed: set[str] = set()
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        entries = load_verified_pool()
        ddgs_first = sorted(entries, key=lambda e: (not e.ddgs_ok, e.latency_ms or 9999))
        self._queue.extend(ddgs_first)

    def reload(self) -> int:
        self._queue.clear()
        self._failed.clear()
        self._loaded = False
        self._ensure_loaded()
        return len(self._queue)

    def has_proxies(self) -> bool:
        self._ensure_loaded()
        return any(e.url not in self._failed for e in self._queue)

    def proxies_in_order(self) -> list[str]:
        """All cached proxies (ddgs-verified first), excluding session failures."""
        self._ensure_loaded()
        ordered = sorted(
            list(self._queue),
            key=lambda e: (not e.ddgs_ok, e.latency_ms or 9999),
        )
        return [e.url for e in ordered if e.url not in self._failed]

    def next_proxy(self) -> str | None:
        self._ensure_loaded()
        while self._queue:
            entry = self._queue.popleft()
            self._queue.append(entry)
            if entry.url in self._failed:
                continue
            return entry.url
        return None

    def mark_failed(self, proxy_url: str) -> None:
        if proxy_url:
            self._failed.add(proxy_url)


_funnel: ProxyFunnel | None = None


def get_proxy_funnel() -> ProxyFunnel:
    global _funnel
    if _funnel is None:
        _funnel = ProxyFunnel()
    return _funnel


def resolve_ddgs_proxies_to_try() -> list[str | None]:
    """
    Order for ddgs.text():
      - Pool enabled: verified proxies first, then optional direct
      - Pool off: explicit DDGS_PROXY, else direct
    """
    use_pool = proxy_pool_enabled()
    allow_direct = _env_true(
        "DDGS_ALLOW_DIRECT",
        "false" if use_pool else "true",
    )

    explicit = (os.getenv("DDGS_PROXY") or "").strip()
    if not explicit and not use_pool:
        for key in ("HTTPS_PROXY", "https_proxy"):
            explicit = (os.getenv(key) or "").strip()
            if explicit:
                break

    chain: list[str | None] = []
    seen: set[str] = set()

    def _add(url: str | None) -> None:
        if url is None:
            return
        norm = _normalize_proxy_url(url) or url
        if norm not in seen:
            seen.add(norm)
            chain.append(norm)

    if use_pool:
        if not get_proxy_funnel().has_proxies() and _env_true("DDGS_AUTO_PROXY"):
            ensure_proxy_pool_ready()
        for p in get_proxy_funnel().proxies_in_order():
            _add(p)
        max_rot = int(os.getenv("DDGS_PROXY_ROTATIONS", "8"))
        if len(chain) > max_rot:
            chain = chain[:max_rot]

    if explicit:
        _add(explicit)
        # Prefer explicit first when user set DDGS_PROXY manually
        if explicit in seen:
            chain = [explicit] + [p for p in chain if p != explicit]

    if allow_direct:
        chain.append(None)
    elif not chain:
        chain.append(None)

    return chain


def best_proxy_for_ddgs(
    *,
    max_check: int = 40,
    test_ddgs: bool = True,
    log_print: Callable[[str], None] | None = None,
) -> ProxyEntry | None:
    return run_proxy_pipeline(
        max_check=max_check,
        test_ddgs=test_ddgs,
        log_print=log_print,
    ).best
