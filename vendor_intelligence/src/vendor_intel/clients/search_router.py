"""Web search router — primary: ddgs library; optional fallbacks in search_router only."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from vendor_intel.clients.duckduckgo import (
    ddgs_backend_param,
    duckduckgo_available,
    duckduckgo_search,
    duckduckgo_search_many,
)
from vendor_intel.clients.host_reachability import (
    print_connectivity_report,
    searxng_local_reachable,
    searxng_search_usable,
)
from vendor_intel.clients.searxng import searxng_search_any, searxng_urls
from vendor_intel.clients.wikipedia_search import wikipedia_search
from vendor_intel.config import Settings
from vendor_intel.mock.fixtures import is_mock_run

_probe_printed = False


def _geo_suffix(geo: str) -> str:
    """Space-prefixed geography for appending to queries (never strip the leading space)."""
    g = (geo or "").strip()
    if not g or g.lower() == "global":
        return ""
    return f" {g}"


def _backup_hit_threshold(*, discovery_mode: bool, validation_mode: bool, default_min: int) -> int:
    if validation_mode:
        return 1
    if discovery_mode:
        try:
            return max(1, int(os.getenv("DISCOVERY_MIN_BEFORE_SEARXNG", "2") or "2"))
        except ValueError:
            return 2
    return default_min


@dataclass
class SearchResult:
    title: str
    link: str
    snippet: str
    backend: str


def _query_variants(
    query: str,
    market: str,
    geo: str,
    *,
    validation_mode: bool = False,
    discovery_mode: bool = False,
) -> list[str]:
    base = query.strip()
    if validation_mode or discovery_mode:
        return [base] if base else []

    variants = [base]
    tail = _geo_suffix(geo)
    if market and market.lower() not in base.lower():
        variants.append(f"{market}{tail}".strip())
    variants.extend([f"list of {base}", f"top {base}"])
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        key = v.lower()
        if key in seen or not v:
            continue
        seen.add(key)
        out.append(v)
    return out[:4]


def _log(msg: str) -> None:
    print(f"  [search] {msg}", flush=True)


def _env_true(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def skip_ddgs(*, discovery_mode: bool = False) -> bool:
    """Skip ddgs package (use router fallbacks only)."""
    if _env_true("SKIP_DDGS") or _env_true("SKIP_DUCKDUCKGO"):
        return True
    if discovery_mode and _env_true("DISCOVERY_SKIP_DDGS"):
        return True
    return False


def search_fallbacks_enabled() -> bool:
    """When false, only ddgs (+ Wikipedia thin-result helper) — no Bing HTML / SearXNG."""
    return _env_true("SEARCH_USE_FALLBACKS", default=True)


def search_stack_description(settings: Settings) -> str:
    if skip_ddgs():
        parts = []
        if search_fallbacks_enabled():
            if not _env_true("SKIP_BING_HTML"):
                parts.append("Bing HTML")
            if settings.searxng_base_url:
                parts.append("SearXNG")
        return "SKIP_DDGS → " + (" → ".join(parts) if parts else "none")
    order = [f"ddgs (backend={ddgs_backend_param()})"]
    if search_fallbacks_enabled():
        if not _env_true("SKIP_BING_HTML"):
            order.append("Bing HTML")
        if settings.searxng_base_url:
            order.append("SearXNG")
    order.append("Wikipedia (thin results)")
    return " → ".join(order)


# Per-run in-memory search-result cache. Phase 1, Phase 2, directory/major-player mining and
# seed resolution build their own routers and frequently issue the SAME query; a hit returns the
# already-filtered results instead of hitting the search backends again. Cleared at the start of
# every pipeline run (clear_search_cache). Disable with SEARCH_CACHE=0.
_SEARCH_CACHE: dict[tuple, list] = {}
_SEARCH_CACHE_STATS = {"hits": 0, "misses": 0}


def _search_cache_enabled() -> bool:
    return os.getenv("SEARCH_CACHE", "1").strip().lower() not in ("0", "false", "no", "off")


def _cache_key(query: str, market: str, geo: str, search_topic: str, discovery_mode: bool, validation_mode: bool) -> tuple:
    norm = lambda x: " ".join(str(x or "").lower().split())  # noqa: E731
    return (norm(query), norm(market), norm(geo), norm(search_topic), bool(discovery_mode), bool(validation_mode))


def clear_search_cache() -> None:
    _SEARCH_CACHE.clear()
    _SEARCH_CACHE_STATS["hits"] = 0
    _SEARCH_CACHE_STATS["misses"] = 0


def search_cache_stats() -> dict[str, int]:
    return {**_SEARCH_CACHE_STATS, "entries": len(_SEARCH_CACHE)}


class FreeSearchRouter:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._num = settings.results_per_query
        self._min = settings.min_results_before_backup
        self._searx_urls = searxng_urls(settings.searxng_base_url)
        self._searxng_tcp = searxng_local_reachable() and bool(self._searx_urls)
        self._searxng_up = self._searxng_tcp and searxng_search_usable()
        prefer = os.getenv("PREFER_SEARXNG", "").strip().lower()
        self._prefer_searxng = prefer in ("1", "true", "yes") or (
            prefer not in ("0", "false", "no") and self._searxng_up
        )
        self._skip_ddgs = skip_ddgs(discovery_mode=False)

    async def _try_searxng(
        self,
        queries: list[str],
        out: list[SearchResult],
        seen: set[str],
        target: int,
        fetch_n: int,
    ) -> bool:
        if not self._searx_urls:
            return False

        def add(backend: str, title: str, link: str, snippet: str) -> None:
            key = link.split("#")[0].rstrip("/").lower()
            if key in seen or not link:
                return
            seen.add(key)
            out.append(SearchResult(title=title, link=link, snippet=snippet, backend=backend))

        for q in queries[:2]:
            if len(out) >= target:
                break
            rows, used = await searxng_search_any(q, self._searx_urls, max_results=fetch_n)
            for row in rows:
                add("searxng", row.title, row.link, row.snippet)
            if rows:
                _log(f"SearXNG OK via {used} ({len(rows)} hits) for {q[:45]!r}")
                return True
        return False

    async def search(
        self,
        query: str,
        *,
        after_days: int | None = None,
        market: str = "",
        geo: str = "",
        search_topic: str = "",
        discovery_mode: bool = False,
        validation_mode: bool = False,
    ) -> list[SearchResult]:
        key = _cache_key(query, market, geo, search_topic, discovery_mode, validation_mode)
        if _search_cache_enabled() and key in _SEARCH_CACHE:
            _SEARCH_CACHE_STATS["hits"] += 1
            return list(_SEARCH_CACHE[key])
        _SEARCH_CACHE_STATS["misses"] += 1
        rows = await self._search_uncached(
            query,
            after_days=after_days,
            market=market,
            geo=geo,
            search_topic=search_topic,
            discovery_mode=discovery_mode,
            validation_mode=validation_mode,
        )
        if _search_cache_enabled() and rows:
            _SEARCH_CACHE[key] = list(rows)
        return rows

    async def _search_uncached(
        self,
        query: str,
        *,
        after_days: int | None = None,
        market: str = "",
        geo: str = "",
        search_topic: str = "",
        discovery_mode: bool = False,
        validation_mode: bool = False,
    ) -> list[SearchResult]:
        global _probe_printed
        del after_days

        if not is_mock_run(self._settings) and not _probe_printed:
            print_connectivity_report()
            _log(f"Search stack: {search_stack_description(self._settings)}")
            if self._skip_ddgs:
                _log("SKIP_DDGS=true — ddgs library not used")
            else:
                _log(
                    f"Primary: ddgs.text() backend={ddgs_backend_param()!r} "
                    f"(see https://github.com/deedy5/ddgs)"
                )
            if search_fallbacks_enabled() and not self._skip_ddgs:
                _log("Fallbacks (if ddgs < min hits): Bing HTML, SearXNG")
            elif not search_fallbacks_enabled():
                _log("SEARCH_USE_FALLBACKS=false — ddgs only (+ Wikipedia)")
            if self._searxng_up:
                _log("SearXNG local is up")
            elif self._searxng_tcp and self._searx_urls:
                _log(
                    "SearXNG port open but 0 test hits — docker compose down && docker compose up -d"
                )
            _probe_printed = True

        out: list[SearchResult] = []
        seen: set[str] = set()

        def add(backend: str, title: str, link: str, snippet: str) -> None:
            key = link.split("#")[0].rstrip("/").lower()
            if key in seen or not link:
                return
            seen.add(key)
            out.append(SearchResult(title=title, link=link, snippet=snippet, backend=backend))

        if validation_mode:
            fetch_n = max(self._num, 10)
            target = fetch_n
        elif discovery_mode:
            fetch_n = max(self._num * 2, 30)
            target = fetch_n
        else:
            fetch_n = max(self._num * 2, 20)
            target = fetch_n
        mock = is_mock_run(self._settings)
        skip_ddgs_this = skip_ddgs(discovery_mode=discovery_mode)
        backup_min = _backup_hit_threshold(
            discovery_mode=discovery_mode,
            validation_mode=validation_mode,
            default_min=self._min,
        )
        q_variants = _query_variants(
            query,
            market,
            geo,
            validation_mode=validation_mode,
            discovery_mode=discovery_mode,
        )
        skip_bing = _env_true("SKIP_BING_HTML")

        async def _try_bing_html(force: bool = False) -> None:
            if mock or len(out) >= target:
                return
            if skip_bing and not force:
                return
            from vendor_intel.clients.bing_html import search_bing_html_try

            bing_variants = q_variants[:3] if discovery_mode else q_variants[:2]
            for bq in bing_variants:
                if len(out) >= target:
                    break
                rows = search_bing_html_try(
                    bq,
                    max_results=min(fetch_n, 15),
                    geo=geo,
                    force=force,
                )
                for r in rows:
                    add("bing_html", r.title, r.link, r.snippet)

        async def _try_ddgs() -> None:
            if mock or skip_ddgs_this or len(out) >= target or not duckduckgo_available():
                return
            max_ddgs_variants = 2 if discovery_mode else (1 if validation_mode else 2)
            for i, q in enumerate(q_variants[:max_ddgs_variants]):
                if len(out) >= target:
                    break
                if i > 0 and len(out) >= self._min:
                    break
                need = min(target - len(out), self._num + 5)
                batch = await duckduckgo_search(q, max_results=need, geo=geo)
                for row in batch:
                    add(getattr(row, "engine", None) or "ddgs", row.title, row.link, row.snippet)
                if not batch and i == 0 and len(out) < self._min:
                    _log(f"DDGS returned 0 for {q[:50]!r}")

        use_fallbacks = search_fallbacks_enabled() and not mock

        if skip_ddgs_this:
            if use_fallbacks:
                await _try_bing_html()
                if (
                    len(out) < backup_min
                    and self._searx_urls
                    and (self._searxng_up or self._searxng_tcp)
                ):
                    _log("Fallback: SearXNG")
                    await self._try_searxng(q_variants, out, seen, target, fetch_n)
                if len(out) < backup_min and skip_bing:
                    await _try_bing_html(force=True)
        else:
            if use_fallbacks and self._prefer_searxng and self._searxng_up and len(out) < backup_min:
                await self._try_searxng(q_variants, out, seen, target, fetch_n)
            await _try_ddgs()
            if use_fallbacks and len(out) < backup_min:
                if skip_bing:
                    _log("Fallback: Bing HTML (ddgs returned few results)")
                    await _try_bing_html(force=True)
                else:
                    await _try_bing_html()
            if (
                use_fallbacks
                and len(out) < backup_min
                and self._searx_urls
                and (self._searxng_up or self._searxng_tcp)
                and not self._prefer_searxng
            ):
                _log("Fallback: SearXNG")
                await self._try_searxng(q_variants, out, seen, target, fetch_n)

        wiki_threshold = self._min if discovery_mode else max(2, self._min // 2)
        if not mock and len(out) < wiki_threshold:
            for row in await wikipedia_search(query, max_results=self._num, geo=geo):
                add("wikipedia", row.title, row.link, row.snippet)

        if not mock and len(out) < backup_min:
            tips: list[str] = []
            if not skip_ddgs_this:
                tips.append(
                    f"try DDGS_BACKENDS=bing,mojeek and DDGS_TIMEOUT=30 "
                    f"(current backend={ddgs_backend_param()!r})"
                )
            if skip_ddgs_this:
                tips.append("discovery uses Bing HTML first (DISCOVERY_SKIP_DDGS or SKIP_DDGS)")
            if use_fallbacks:
                if self._searxng_tcp and not self._searxng_up:
                    tips.append("restart SearXNG: docker compose down && docker compose up -d")
                elif not self._searxng_tcp:
                    tips.append("start SearXNG: docker compose up -d")
                if skip_bing:
                    tips.append("set SKIP_BING_HTML=false for Bing HTML fallback")
            _log("Few results — " + "; ".join(tips))

        from vendor_intel.clients.search_relevance import filter_search_results

        if validation_mode:
            min_keep = 1
        elif discovery_mode:
            min_keep = 1
        else:
            min_keep = 5
        if len(out) < min_keep:
            min_keep = max(1, len(out))

        return filter_search_results(
            query,
            market or query,
            geo,
            out,
            search_topic=search_topic,
            min_keep=min_keep,
            require_geo_match=discovery_mode and bool(geo) and geo.lower() != "global",
        )[: self._num]

    async def search_batch(
        self,
        queries: list[str],
        *,
        market: str = "",
        geo: str = "",
        search_topic: str = "",
        discovery_mode: bool = False,
    ) -> dict[str, list[SearchResult]]:
        """Parallel discovery searches — same filters as search(), Phase 2 only."""
        clean = [q.strip() for q in queries if (q or "").strip()]
        if not clean:
            return {}
        cached: dict[str, list[SearchResult]] = {}
        if _search_cache_enabled():
            for q in clean:
                k = _cache_key(q, market, geo, search_topic, discovery_mode, False)
                if k in _SEARCH_CACHE:
                    cached[q] = list(_SEARCH_CACHE[k])
            if cached:
                _SEARCH_CACHE_STATS["hits"] += len(cached)
                clean = [q for q in clean if q not in cached]
                if not clean:
                    return cached

        mock = is_mock_run(self._settings)
        skip_ddgs_this = skip_ddgs(discovery_mode=discovery_mode)
        use_fallbacks = search_fallbacks_enabled() and not mock
        backup_min = _backup_hit_threshold(
            discovery_mode=discovery_mode,
            validation_mode=False,
            default_min=self._min,
        )

        if discovery_mode:
            fetch_n = max(self._num * 2, 30)
        else:
            fetch_n = max(self._num * 2, 20)

        out_map: dict[str, list[SearchResult]] = {q: [] for q in clean}

        if not mock and not skip_ddgs_this and duckduckgo_available():
            try:
                worker_count = int(os.getenv("DDG_WORKER_COUNT", "0") or "0")
            except ValueError:
                worker_count = 0
            if worker_count > 0 and len(clean) > 1:
                raw_batches = await duckduckgo_search_many(
                    clean, max_results=fetch_n, geo=geo
                )
                for q in clean:
                    seen: set[str] = set()
                    bucket: list[SearchResult] = []
                    for row in raw_batches.get(q) or []:
                        key = row.link.split("#")[0].rstrip("/").lower()
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        bucket.append(
                            SearchResult(
                                title=row.title,
                                link=row.link,
                                snippet=row.snippet,
                                backend=row.engine or "ddgs",
                            )
                        )
                    out_map[q] = bucket

        from vendor_intel.clients.search_relevance import filter_search_results

        min_keep = 1 if discovery_mode else 5

        async def _finalize_one(q: str) -> None:
            out = out_map.get(q) or []
            if len(out) < backup_min and use_fallbacks:
                out = await self.search(
                    q,
                    market=market,
                    geo=geo,
                    search_topic=search_topic,
                    discovery_mode=discovery_mode,
                )
            else:
                keep = max(1, min_keep) if len(out) < min_keep else min_keep
                out = filter_search_results(
                    q,
                    market or q,
                    geo,
                    out,
                    search_topic=search_topic,
                    min_keep=keep,
                    require_geo_match=discovery_mode and bool(geo) and geo.lower() != "global",
                )[: self._num]
            out_map[q] = out

        await asyncio.gather(*[_finalize_one(q) for q in clean])
        if _search_cache_enabled():
            for q in clean:
                if out_map.get(q):
                    _SEARCH_CACHE[_cache_key(q, market, geo, search_topic, discovery_mode, False)] = list(out_map[q])
        out_map.update(cached)
        return out_map
