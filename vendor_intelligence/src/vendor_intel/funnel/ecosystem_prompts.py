"""Build search prompts from LLM scope fields — specific queries, no listicle wording."""
from __future__ import annotations

import re

from vendor_intel.discovery.discovery_query_quality import (
    is_listicle_discovery_query,
    sanitize_discovery_query,
)
from vendor_intel.funnel.prompt_builder import _q, geo_search_label, refine_search_topic


def _role_specific_query(role: str, topic: str, g: str, kw: str) -> str:
    """Map ecosystem role → specific search query (avoids generic 'companies India')."""
    r = (role or "").lower()
    t = topic or kw
    if re.search(r"\bapi\b|active\s+pharmaceutical", r):
        return _q("API manufacturers", g, t, "GMP", "official", "site")
    if "contract" in r or "cdmo" in r or "cmo" in r:
        return _q("contract manufacturing", t, g, "GMP", "CDMO")
    if "export" in r:
        return _q(t, "exporter", g, "WHO", "GMP", "certified")
    if "import" in r:
        return _q(t, "importer", g, "registered", "company")
    if "distribut" in r or "wholesal" in r:
        return _q("authorized", t, "distributor", g, "firm", "official")
    if "retail" in r:
        return _q(t, "retail", "pharmacy", g, "chain", "corporate")
    if "research" in r or "cro" in r:
        return _q("contract research", t, g, "CRO", "organization")
    if "manufactur" in r or "formulation" in r:
        return _q(t, "manufacturer", g, "formulation", "official", "site")
    if "biotech" in r or "biosimilar" in r:
        return _q("biotech", t, "companies", g, "R&D", "pipeline")
    if "integrat" in r or "mssp" in r or "service" in r:
        return _q(t, "service provider", g, "official", "website")
    if re.search(r"cyber\w*|infosec|information\s+security|network\s+security|"
                 r"data\s+security|endpoint\s+security|cloud\s+security", r):
        return _q(t, "security vendor", g, "corporate", "site")
    return _q(kw, role, g, "official", "website")


def build_prompts_from_ecosystem(
    market: str,
    geo: str,
    ecosystem_functions: list[str],
    *,
    industry_terms: list[str] | None = None,
    max_prompts: int = 12,
) -> list[dict[str, str]]:
    """One specific discovery prompt per value-chain role in scope.ecosystem_functions."""
    g = geo_search_label(geo)
    topic = refine_search_topic(market, geo)
    terms = [t for t in (industry_terms or []) if t and len(str(t).strip()) >= 3]
    kw = terms[0] if terms else topic

    seen: set[str] = set()
    out: list[dict[str, str]] = []

    def push(text: str) -> None:
        text = sanitize_discovery_query(text)
        if not text or is_listicle_discovery_query(text):
            return
        key = " ".join(text.lower().split())
        if not text or key in seen or len(text) < 8:
            return
        seen.add(key)
        out.append({"id": f"P{len(out) + 1}", "level": "discovery", "text": text})

    for role in ecosystem_functions:
        if len(out) >= max_prompts:
            break
        role = str(role or "").strip()
        if len(role) < 3:
            continue
        push(_role_specific_query(role, topic, g, kw))

    for term in terms[1:4]:
        if len(out) >= max_prompts:
            break
        push(_q(term, g, "manufacturer", "official", "site"))

    if len(out) < max_prompts:
        push(_q(kw, g, "headquarters", "corporate", "website"))

    for i, row in enumerate(out):
        row["id"] = f"P{i + 1}"
    return out[:max_prompts]
