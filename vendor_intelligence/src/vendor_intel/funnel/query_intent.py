"""Parse user query — generic rules only; no hardcoded country or industry lists."""
from __future__ import annotations

import re
from typing import Any

_FILLER_PREFIX = re.compile(
    r"^(?:please\s+)?(?:give\s+me\s+(?:the\s+)?|find\s+(?:me\s+)?(?:the\s+)?|"
    r"show\s+(?:me\s+)?(?:the\s+)?|list\s+(?:the\s+)?|what\s+are\s+(?:the\s+)?|"
    r"i\s+want\s+(?:the\s+)?|looking\s+for\s+(?:the\s+)?)",
    re.I,
)
_FILLER_SUFFIX = re.compile(r"\s+(?:please|thanks|thank you)\.?$", re.I)
_LEADING_QUALIFIER = re.compile(
    r"^(?:best|top|leading|popular|biggest|main|major)\s+",
    re.I,
)
_ROLE_NOUN = re.compile(
    r"\b(?:manufacturers?|suppliers?|exporters?|producers?|vendors?|"
    r"companies|brands?|startups?)\b",
    re.I,
)
# "in Nova Scotia, Canada" / "in Assam, India"
_GEO_COMMA = re.compile(
    r"\s+in\s+(.+?),\s*([A-Za-z][A-Za-z\s\-]{1,48})\s*$",
    re.I,
)
# "in Iceland" / "in Kenya" / "in Shizuoka Prefecture" (no trailing country)
_GEO_SIMPLE = re.compile(
    r"\s+in\s+([A-Za-z][A-Za-z\s\-]{2,60})\s*$",
    re.I,
)
# Named company in competitor-style queries (generic phrasing only)
_ANCHOR_PATTERNS = (
    re.compile(r"\bcompetitors?\s+of\s+(.+?)(?:\s+in\s+|\s*$)", re.I),
    re.compile(r"\balternatives?\s+to\s+(.+?)(?:\s+in\s+|\s*$)", re.I),
    re.compile(r"^(.+?)\s+competitors?\b", re.I),
    re.compile(r"\bvs\.?\s+(.+?)(?:\s+in\s+|\s*$)", re.I),
)


def _title_words(text: str) -> str:
    raw = text.strip()
    upper = {w.upper() for w in raw.split()}
    if upper == {"USA"} or upper == {"US"}:
        return "United States"
    if upper == {"UK"}:
        return "United Kingdom"
    return " ".join(w.capitalize() for w in raw.split())


def _normalize_query(query: str) -> str:
    q = query.strip()
    q = _FILLER_PREFIX.sub("", q).strip()
    q = _FILLER_SUFFIX.sub("", q).strip()
    return q


def parse_query_parts(query: str) -> tuple[str, str]:
    """
    Split query into (market_topic, geography).
    Geography is a single display string, e.g. "Nova Scotia, Canada" or "Iceland".
    """
    q = _normalize_query(query)
    m = _GEO_COMMA.search(q)
    if m:
        region = m.group(1).strip()
        country = m.group(2).strip()
        geo = _title_words(f"{region}, {country}")
        topic = q[: m.start()].strip()
        return _clean_market_phrase(topic), geo

    m = _GEO_SIMPLE.search(q)
    if m:
        geo = _title_words(m.group(1).strip())
        topic = q[: m.start()].strip()
        return _clean_market_phrase(topic), geo

    return _clean_market_phrase(q), "global"


def _clean_market_phrase(topic: str) -> str:
    t = _LEADING_QUALIFIER.sub("", topic.strip())
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) >= 3 else topic.strip()


def _strip_geo_from_market(market: str, geo: str) -> str:
    """Remove a trailing 'in <geo>' accidentally left inside market text."""
    if not market or geo == "global":
        return market
    low = market.lower()
    for suffix in (f" in {geo.lower()}", f" in {geo.split(',')[0].strip().lower()}"):
        if low.endswith(suffix):
            return _clean_market_phrase(market[: -len(suffix)])
    return market


def extract_geography_from_query(query: str, scope: dict[str, Any] | None = None) -> str:
    if scope and scope.get("geographies"):
        g = scope["geographies"]
        if isinstance(g, list) and g and str(g[0]).strip().lower() not in ("", "global"):
            return str(g[0]).strip()
        if isinstance(g, str) and g.strip().lower() not in ("", "global"):
            return g.strip()
    _, geo = parse_query_parts(query)
    return geo


def extract_market_from_query(query: str) -> str:
    market, _ = parse_query_parts(query)
    return market


def _clean_anchor_phrase(phrase: str) -> str:
    t = _LEADING_QUALIFIER.sub("", phrase.strip())
    t = _ROLE_NOUN.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" ,-")
    return t if len(t) >= 2 else ""


def extract_anchor_company_from_query(query: str) -> str | None:
    """Detect a focal company from competitor-style wording in the query."""
    q = _normalize_query(query)
    for pat in _ANCHOR_PATTERNS:
        m = pat.search(q)
        if not m:
            continue
        name = _clean_anchor_phrase(m.group(1))
        if len(name) >= 2 and len(name.split()) <= 6:
            return _title_words(name)
    return None


def infer_industry_context(market: str, query: str) -> dict[str, Any]:
    """
    Derive industry_vertical + industry_terms from market/query text only
    (no hardcoded country/industry lists).
    """
    from vendor_intel.funnel.prompt_builder import distill_search_topic, topic_variants

    base = distill_search_topic(market or query)
    vertical = refine_search_topic_from_market(base)
    variants = topic_variants(market or query, "")
    terms: list[str] = []
    seen: set[str] = set()
    for v in [vertical, base, *variants]:
        key = v.lower().strip()
        if key and key not in seen and len(key) >= 3:
            seen.add(key)
            terms.append(v)
    low = (market or query).lower()
    if "pharma" in low:
        for extra in ("pharma", "generic drugs", "API manufacturers", "formulations", "biosimilars"):
            if extra not in seen:
                seen.add(extra)
                terms.append(extra)
    return {
        "industry_vertical": vertical,
        "industry_terms": terms[:8],
    }


def refine_search_topic_from_market(market: str) -> str:
    from vendor_intel.funnel.prompt_builder import refine_search_topic

    return refine_search_topic(market, "")


def enrich_scope_from_query(scope: dict[str, Any], query: str) -> dict[str, Any]:
    """Fill or correct scope from query; always reconcile geo when query has 'in …'."""
    parsed_market, parsed_geo = parse_query_parts(query)
    source = scope.get("scope_source", "regex_fallback")

    market = (scope.get("market") or scope.get("product") or "").strip()
    if not market or str(market).lower() in ("", "general"):
        market = parsed_market
    else:
        market = _strip_geo_from_market(str(market), parsed_geo)
        low = market.lower()
        geo_leaked = parsed_geo != "global" and (
            f" in {parsed_geo.lower()}" in low
            or any(
                part.strip().lower() in low
                for part in parsed_geo.split(",")
                if len(part.strip()) > 3
            )
        )
        if geo_leaked and len(parsed_market) >= 3:
            market = parsed_market

    geos = scope.get("geographies")
    if not isinstance(geos, list) or not geos:
        geos = [parsed_geo]
    elif (
        parsed_geo != "global"
        and len(geos) == 1
        and str(geos[0]).strip().lower() in ("", "global")
    ):
        geos = [parsed_geo]
    elif parsed_geo != "global" and parsed_geo not in geos:
        geos = [parsed_geo] + [g for g in geos if str(g).lower() != "global"]

    geo_label = geos[0] if geos else parsed_geo
    summary = (scope.get("interpretation_summary") or "").strip()
    if not summary or len(summary) < 12 or "global" in summary.lower() and parsed_geo != "global":
        summary = (
            f"Market map: {market} in {geo_label}"
            if geo_label != "global"
            else f"Market map: {market}"
        )

    out_source = source
    if source not in ("llm", "mock") and parsed_geo != "global":
        out_source = scope.get("scope_source", "regex_fallback")

    from vendor_intel.funnel.prompt_builder import refine_search_topic

    search_topic = refine_search_topic(market, geo_label)

    anchor = (scope.get("anchor_company") or "").strip()
    if not anchor:
        detected = extract_anchor_company_from_query(query)
        if detected:
            anchor = detected

    intent = scope.get("intent") or "market_map"
    if anchor and "competitor" in query.lower():
        intent = "competitor_set"

    out: dict[str, Any] = {
        **scope,
        "market": market,
        "search_topic": search_topic,
        "geographies": geos[:3],
        "interpretation_summary": summary,
        "funnel_enabled": True,
        "query_parsed": True,
        "scope_source": out_source,
        "intent": intent,
    }
    if anchor:
        out["anchor_company"] = anchor

    if not out.get("industry_vertical"):
        ctx = infer_industry_context(market, query)
        out["industry_vertical"] = ctx["industry_vertical"]
        if not out.get("industry_terms"):
            out["industry_terms"] = ctx["industry_terms"]
    elif not out.get("industry_terms"):
        out["industry_terms"] = infer_industry_context(market, query)["industry_terms"]

    return out


def build_generic_funnel_prompts(
    market: str,
    geo: str,
    *,
    industry_terms: list[str] | None = None,
) -> list[dict[str, str]]:
    from vendor_intel.funnel.prompt_builder import build_funnel_prompts

    return build_funnel_prompts(market, geo, industry_terms=industry_terms)


def build_generic_discovery_prompts(
    market: str,
    geo: str,
    funnel_prompts: list[dict[str, str]] | None = None,
    *,
    anchor_company: str | None = None,
    max_prompts: int = 9,
    industry_terms: list[str] | None = None,
) -> list[dict[str, str]]:
    del funnel_prompts
    from vendor_intel.funnel.prompt_builder import build_discovery_prompts

    return build_discovery_prompts(
        market,
        geo,
        max_prompts=max_prompts,
        anchor_company=anchor_company,
        industry_terms=industry_terms,
    )
