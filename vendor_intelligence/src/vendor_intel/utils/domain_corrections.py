"""Fix wrong SERP domains and prefer resolvable hosts (especially Brazil .com.br)."""
from __future__ import annotations

import socket
from typing import Iterable

# Wrong discovery / LLM seed domains → official sites
_KNOWN_ALIASES: dict[str, str] = {
    "braskem.com": "www.braskem.com.br",
    "www.braskem.com": "www.braskem.com.br",
    "braskem.com.br": "www.braskem.com.br",
    "raizen.com": "raizen.com.br",
    "oxiteno.com": "oxiteno.com.br",
}

# Global brands: keep .com; only apply .com.br when the .com host does not resolve
_GLOBAL_KEEP_COM = frozenset(
    {
        "dow.com",
        "solvay.com",
        "pbpc.com",
        "dow.com.br",
    }
)


def _normalize_host(domain: str) -> str:
    d = (domain or "").strip().lower()
    for p in ("https://", "http://"):
        if d.startswith(p):
            d = d[len(p) :]
    if d.startswith("www."):
        d = d[4:]
    return d.split("/")[0].split("?")[0].strip(".")


def _resolve_ipv4(host: str, timeout: float) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False


def hostname_resolves(host: str, timeout: float = 5.0) -> bool:
    """True if host or www.{host} has an IPv4 address (for connectivity checks)."""
    return bool(crawl_host_for_domain(host, timeout=timeout))


def crawl_host_for_domain(domain: str, *, timeout: float = 5.0) -> str:
    """
    Host to pass to smart_crawl / httpx.

    Many sites (e.g. braskem.com.br) only have DNS on www.* — apex nslookup
    shows a name but no A record; use www. when apex does not resolve.
    """
    d = _normalize_host(domain)
    if not d:
        return d
    try:
        if _resolve_ipv4(d, timeout):
            return d
        if not d.startswith("www."):
            www = f"www.{d}"
            if _resolve_ipv4(www, timeout):
                return www
        return d
    finally:
        try:
            socket.setdefaulttimeout(None)
        except Exception:
            pass


def alias_domain(domain: str) -> str:
    d = _normalize_host(domain)
    return _KNOWN_ALIASES.get(d, d)


def brazil_com_br_candidate(domain: str) -> str | None:
    """braskem.com → braskem.com.br when country is Brazil."""
    d = _normalize_host(domain)
    if not d.endswith(".com") or d in _GLOBAL_KEEP_COM:
        return None
    if d.count(".") != 1:
        return None
    base = d[:-4]
    if not base or len(base) < 3:
        return None
    return f"{base}.com.br"


def resolve_best_domain(
    domain: str,
    *,
    country: str = "",
    name: str = "",
    recall_mode: bool = False,
) -> str:
    """
    Pick a domain that resolves: alias map → .com.br (Brazil) → original.

    recall_mode: only known aliases + www fix (no guessing *.com.br from *.com).
    """
    d = alias_domain(domain)
    country_low = (country or "").strip().lower()
    candidates: list[str] = [d]

    if not recall_mode and country_low in ("brazil", "brasil", "br"):
        br = brazil_com_br_candidate(d)
        if br and br not in candidates:
            candidates.insert(0, br)

    for alt in candidates:
        crawl = crawl_host_for_domain(alt)
        if _resolve_ipv4(crawl, 5.0) or (crawl.startswith("www.") and crawl != alt):
            if crawl != _normalize_host(domain):
                print(f"  [domain] {domain} -> {crawl} (DNS)", flush=True)
            return crawl

    return crawl_host_for_domain(d)


def fix_company_domain(
    company: dict[str, str],
    *,
    country: str = "",
    recall_mode: bool = False,
) -> dict[str, str]:
    name = str(company.get("name") or "").strip()
    dom = str(company.get("domain") or "").strip()
    if not dom:
        return company
    fixed = resolve_best_domain(dom, country=country, name=name, recall_mode=recall_mode)
    if fixed != dom:
        return {**company, "domain": fixed, "domain_corrected_from": dom}
    return company


def fix_company_list(
    companies: list[dict[str, str]],
    *,
    country: str = "",
    recall_mode: bool = False,
) -> list[dict[str, str]]:
    """Domain fix for many companies. Each fix may do DNS lookups (up to 5s on failure), so they
    run in a bounded thread pool; output order matches input order."""
    items = list(companies or [])
    if len(items) <= 1:
        return [fix_company_domain(c, country=country, recall_mode=recall_mode) for c in items]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(16, len(items))) as ex:
        return list(
            ex.map(lambda c: fix_company_domain(c, country=country, recall_mode=recall_mode), items)
        )
