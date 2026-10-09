"""Inject Phase 1 LLM seed companies (priority, correct domains)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vendor_intel.discovery.company_registry import normalize_seed_companies
from vendor_intel.funnel.scope_schema import normalize_run_scope


def load_scope_from_plan(plan_path: str | None) -> dict[str, Any]:
    if not plan_path:
        return {}
    p = Path(plan_path)
    if not p.is_file():
        return {}
    try:
        plan = json.loads(p.read_text(encoding="utf-8"))
        return normalize_run_scope(dict(plan.get("scope") or {}), str(plan.get("query") or ""))
    except Exception:
        return {}


def seed_companies_from_scope(scope: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for display, domain, fn in normalize_seed_companies(scope.get("seed_companies")):
        if not display or not domain:
            continue
        rows.append(
            {
                "name": display,
                "domain": domain.lower().removeprefix("www."),
                "discovery_source": "phase1_seed",
                "company_function": fn or "",
                "is_seed": True,
            }
        )
    return rows


def _nk(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _clean_domain(d: str) -> str:
    d = str(d or "").strip().lower()
    d = d.removeprefix("http://").removeprefix("https://").removeprefix("www.").split("/")[0].strip()
    return d


def _llm_seed_domains(names: list[str], settings: Any) -> dict[str, str]:
    """Resolve company names to official domains from LLM knowledge (reliable; avoids the junk
    that degraded web search returns - HP Lubricants->hp.com, Idemitsu->poki.com, etc.).
    Returns {normalized_name: domain}."""
    try:
        from vendor_intel.clients.claude import ClaudeClient

        c = ClaudeClient(settings)
        if not getattr(c, "available", False):
            return {}
        sys = (
            "For each company, return its single OFFICIAL primary website domain (lowercase, no "
            "http/www, no path). Use the company's REAL corporate domain - e.g. Castrol->castrol.com, "
            "HP Lubricants->hpcl.in, Idemitsu->idemitsu.com, Safety-Kleen->safety-kleen.com, "
            "Gulf Oil->gulfoil.com. NEVER a directory, marketplace, news, or unrelated company. If you "
            'are not confident, use "". '
            'Return ONLY JSON: {"domains":[{"name":"<exact input name>","domain":"<domain or empty>"}]}'
        )
        # Chunk so a long list (e.g. directory-mined names) never overflows max_tokens and
        # truncates to invalid JSON — which would silently drop the whole batch to web-search.
        clean = [str(n).strip() for n in (names or []) if str(n).strip()]
        m: dict[str, str] = {}
        for i in range(0, len(clean), 30):
            batch = clean[i : i + 30]
            user = "COMPANIES:\n" + "\n".join(f"- {n}" for n in batch)
            try:
                out = c.complete_json(sys, user, model="claude-haiku-4-5-20251001", max_tokens=4096)
            except Exception:
                continue
            rows = out.get("domains") if isinstance(out, dict) else out
            for r in rows or []:
                if not isinstance(r, dict):
                    continue
                nm, dom = _nk(r.get("name")), _clean_domain(r.get("domain"))
                if nm and dom and "." in dom:
                    m[nm] = dom
        return m
    except Exception:
        return {}


async def resolve_user_seeds(
    names: list[str], settings: Any, country: str = "global", *, search_fallback: bool = True
) -> tuple[list[dict[str, str]], list[str]]:
    """Resolve analyst-provided company names to {canonical_name, primary_domain}.

    LLM knowledge first (accurate official domains for known companies), then a web-search
    fallback for any the model doesn't know. Returns (resolved, unresolved_names).
    """
    import asyncio

    from vendor_intel.clients.search_router import FreeSearchRouter
    from vendor_intel.discovery.company_registry import (
        is_blocklisted_domain,
        resolve_official_domain,
    )
    from vendor_intel.utils.domain_corrections import hostname_resolves
    from vendor_intel.utils.domains import domain_from_url

    clean = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not clean:
        return [], []

    # Pass 1: official domains from LLM knowledge (reliable; one call, no web search).
    llm_map = await asyncio.to_thread(_llm_seed_domains, clean, settings)

    router = FreeSearchRouter(settings)
    geo = (country or "global").strip()
    out: list[dict[str, str]] = []

    # Pass 1: DNS-verify each LLM domain IN PARALLEL; accept the good ones, queue the rest.
    # (A plausible-but-wrong guess that doesn't resolve falls through to the search fallback.)
    async def _accept_llm(name: str) -> tuple[str, str]:
        dom = llm_map.get(_nk(name), "")
        if dom and not is_blocklisted_domain(dom) and await asyncio.to_thread(hostname_resolves, dom, 3.0):
            return name, dom
        return name, ""

    needs_search: list[str] = []
    for name, dom in await asyncio.gather(*[_accept_llm(n) for n in clean]):
        if dom:
            out.append({"canonical_name": name, "primary_domain": dom, "company_function": ""})
            print(f"  [seeds] resolved '{name}' -> {dom} (LLM)", flush=True)
        else:
            needs_search.append(name)

    # Pass 2: web-search fallback for the rest — run CONCURRENTLY (bounded) so it stays fast.
    unresolved: list[str] = []
    if needs_search and not search_fallback:
        # LLM + DNS only (time-budgeted bulk resolution: one web search per name would starve
        # the shared search backends that discovery needs).
        return out, list(needs_search)
    if needs_search:
        sem = asyncio.Semaphore(8)

        async def _search_one(name: str) -> tuple[str, str]:
            async with sem:
                q = f"{name} official website" + (f" {geo}" if geo and geo.lower() != "global" else "")
                try:
                    rows = await router.search(q, market=name, geo=geo, search_topic=name, discovery_mode=False)
                except Exception:
                    rows = []
                cands: list[str] = []
                for r in rows[:8]:
                    d = domain_from_url(getattr(r, "link", "") or "")
                    if d and not is_blocklisted_domain(d) and d not in cands:
                        cands.append(d)
                # Accept only a domain that matches the company name (never an unrelated top result).
                return name, resolve_official_domain(name, cands)

        for name, dom in await asyncio.gather(*[_search_one(n) for n in needs_search]):
            if dom:
                out.append({"canonical_name": name, "primary_domain": dom, "company_function": ""})
                print(f"  [seeds] resolved '{name}' -> {dom} (search)", flush=True)
            else:
                unresolved.append(name)
                print(f"  [seeds] could not resolve an official domain for '{name}'", flush=True)
    return out, unresolved


def merge_user_seeds_into_scope(scope: dict[str, Any], resolved: list[dict[str, str]]) -> dict[str, Any]:
    """Prepend analyst seeds to scope.seed_companies (deduped by domain)."""
    if not resolved:
        return scope
    existing = list(scope.get("seed_companies") or [])
    have = set()
    for s in existing:
        d = (s.get("primary_domain") if isinstance(s, dict) else "") or ""
        if d:
            have.add(d.lower().removeprefix("www."))
    merged = list(resolved)
    for s in existing:
        d = (s.get("primary_domain") if isinstance(s, dict) else "") or ""
        if d.lower().removeprefix("www.") not in {r["primary_domain"].lower().removeprefix("www.") for r in resolved}:
            merged.append(s)
    scope = dict(scope)
    scope["seed_companies"] = merged
    return scope


def merge_seeds_first(
    companies: list[dict[str, str]],
    scope: dict[str, Any],
) -> tuple[list[dict[str, str]], set[str]]:
    """Seeds at front; dedupe by domain."""
    seeds = seed_companies_from_scope(scope)
    seed_doms = {s["domain"] for s in seeds if s.get("domain")}
    by_dom: dict[str, dict[str, str]] = {}
    for s in seeds:
        dom = s.get("domain") or ""
        if dom:
            by_dom[dom] = s
    for c in companies:
        dom = (c.get("domain") or "").strip().lower()
        if not dom:
            continue
        if dom not in by_dom:
            by_dom[dom] = {**c, "is_seed": False}
    ordered = list(by_dom.values())
    ordered.sort(key=lambda x: (0 if x.get("is_seed") else 1, x.get("name", "")))
    return ordered, seed_doms
