"""Volume discovery prompts — specific role/geo queries; avoid listicle search terms."""
from __future__ import annotations

import re

from vendor_intel.discovery.discovery_query_quality import (
    is_listicle_discovery_query,
    sanitize_discovery_query,
)
from vendor_intel.funnel.prompt_builder import (
    _q,
    geo_search_label,
    refine_search_topic,
    topic_variants,
)


def _is_pharma_market(market: str, terms: list[str]) -> bool:
    # "pharma"/"pharmaceutical" always count; bare "drug" counts UNLESS it's clearly a
    # non-manufacturing context (testing/screening/enforcement/policy/abuse), which is
    # where the old bare-word match used to misfire into unrelated markets.
    blob = f"{market} {' '.join(terms)}".lower()
    return bool(
        re.search(
            r"\b(?:pharma\w*|pharmaceutical\w*|bulk\s+drug|active\s+pharmaceutical\s+ingredient)\b"
            r"|\bdrug\b(?!\s*(?:test|screen|abuse|polic|war|enforcement|awareness|addict))",
            blob,
        )
    )


def _is_cyber_market(market: str, terms: list[str]) -> bool:
    # Tight signals only — bare "security" also matches physical/food/border security etc.
    blob = f"{market} {' '.join(terms)}".lower()
    return bool(
        re.search(
            r"\b(?:cyber\w*|infosec|mssp|siem|information\s+security|network\s+security|"
            r"data\s+security|endpoint\s+security|cloud\s+security)\b",
            blob,
        )
    )


def _is_facade_market(market: str, terms: list[str]) -> bool:
    blob = f"{market} {' '.join(terms)}".lower()
    return bool(
        re.search(
            r"\b(?:cladding|facade|façade|curtain\s*wall|rainscreen|"
            r"composite\s+panel|building\s+envelope|architectural\s+metal)\b",
            blob,
        )
    )


def _facade_diversity_prompts(topic: str, g: str, alt: str) -> list[tuple[str, str, str]]:
    t = topic or alt or "aluminium cladding"
    return [
        ("D1", _q("ACP", "manufacturers", g), "manufacturers"),
        ("D2", _q("aluminium rainscreen", "facade", "systems", "companies", g), "manufacturers"),
        ("D3", _q("architectural aluminium panel", "manufacturers", g), "manufacturers"),
        ("D4", _q("ventilated facade", "system", "suppliers", g), "distributors"),
        ("D5", _q("aluminium composite panel", "manufacturer", g, "official"), "manufacturers"),
        ("D6", _q("curtain wall", "manufacturer", g, "corporate", "site"), "manufacturers"),
        ("D7", _q("aluminium facade", "systems", "companies", g), "integrators"),
        ("D8", _q("building envelope", "fabricator", g, "corporate"), "integrators"),
        ("D9", _q("authorized", t, "distributor", g, "firm"), "distributors"),
        ("D10", _q(t, "fabricator", g, "official", "website"), "manufacturers"),
        ("D11", _q("facade", "contractors", g, "aluminium"), "contractors"),
        ("D12", _q(t, "installer", g, "official", "website"), "contractors"),
    ]


def _is_timing_market(market: str, terms: list[str]) -> bool:
    blob = f"{market} {' '.join(terms)}".lower()
    return bool(
        re.search(
            r"\b(?:atomic\s*clock|rubidium|cesium|hydrogen\s*maser|"
            r"frequency\s*standard|gps\s*disciplined|precision\s*timing|"
            r"time\s*reference|network\s*timing)\b",
            blob,
        )
    )


def _timing_diversity_prompts(topic: str, g: str, alt: str) -> list[tuple[str, str, str]]:
    t = topic or alt or "atomic clocks"
    return [
        ("D1", _q("rubidium atomic clock", "manufacturer", g, "official"), "manufacturers"),
        ("D2", _q("cesium frequency standard", "producer", g, "corporate"), "manufacturers"),
        ("D3", _q("hydrogen maser", "manufacturer", g, "official", "site"), "manufacturers"),
        ("D4", _q("GPS disciplined oscillator", "vendor", g), "integrators"),
        ("D5", _q("network timing", "synchronization", "equipment", g), "integrators"),
        ("D6", _q("telecom timing", "synchronization", "vendor", g), "integrators"),
        ("D7", _q("precision time", "frequency", "standard", g), "manufacturers"),
        ("D8", _q("NTP time server", "atomic", "reference", g), "manufacturers"),
        ("D9", _q("phase noise", "oscillator", "manufacturer", g), "manufacturers"),
        ("D10", _q(t, "timing", "solutions", g, "company"), "integrators"),
    ]


def _is_cnc_sim_market(market: str, terms: list[str]) -> bool:
    blob = f"{market} {' '.join(terms)}".lower()
    return bool(
        re.search(
            r"\b(?:g[\s-]?code|cnc\s*simul|machining\s*simul|cam\s*software|"
            r"toolpath\s*verif|nc\s*simul|post[\s-]?processor)\b",
            blob,
        )
    )


def _cnc_diversity_prompts(topic: str, g: str, alt: str) -> list[tuple[str, str, str]]:
    t = topic or alt or "CNC simulation"
    return [
        ("D1", _q("G-code verification", "software", g, "vendor"), "software_vendors"),
        ("D2", _q("CNC machining simulation", "software", g, "developer"), "software_vendors"),
        ("D3", _q("CAM software", "machining simulation", g, "official"), "software_vendors"),
        ("D4", _q("toolpath verification", "platform", g, "corporate"), "software_vendors"),
        ("D5", _q("NC program simulation", "vendor", g), "software_vendors"),
        ("D6", _q("virtual machining", "simulation", g, "company"), "software_vendors"),
        ("D7", _q("CNC collision detection", "software", g), "software_vendors"),
        ("D8", _q("post processor", "verification", g, "developer"), "integrators"),
        ("D9", _q(t, "software", g, "official", "website"), "software_vendors"),
        ("D10", _q("machine simulation", "CAD CAM", g, "vendor"), "software_vendors"),
    ]


def _is_chemical_market(market: str, terms: list[str]) -> bool:
    # Tight signals only — must NOT fire on battery terms like "lithium polymer"/"LiPo".
    blob = f"{market} {' '.join(terms)}".lower()
    if re.search(r"\b(?:batter\w*|lipo|li-?ion|lithium|cell|bms|drone|uav)\b", blob):
        return False
    return bool(
        re.search(
            r"\b(?:ethylene|olefin|polyolefin|petrochem\w*|bioethanol|"
            r"polyethylene|polypropylene|feedstock|specialty\s+chemical)\b",
            blob,
        )
    )


def _chemical_diversity_prompts(topic: str, g: str, alt: str) -> list[tuple[str, str, str]]:
    """Chemical/materials queries — driven by the actual market topic (never hardcoded)."""
    t = topic or alt or "chemical"
    a = alt or topic or t
    return [
        ("D1", _q(t, "producer", g, "official", "site"), "manufacturers"),
        ("D2", _q(t, "manufacturing", "plant", g), "manufacturers"),
        ("D3", _q(t, "technology", "company", g, "corporate"), "integrators"),
        ("D4", _q(a, "manufacturer", g, "official"), "manufacturers"),
        ("D5", _q(t, "production", "plant", g, "operator"), "manufacturers"),
        ("D6", _q(t, "manufacturer", g, "corporate", "website"), "manufacturers"),
        ("D7", _q(t, "company", g, "headquarters", "profile"), "manufacturers"),
        ("D8", _q(t, "feedstock", "raw material", "supplier", g), "suppliers"),
        ("D9", _q(a, "producer", g, "facility"), "manufacturers"),
        ("D10", _q(t, "distributor", g, "official"), "distributors"),
    ]


def _push(
    out: list[dict[str, str]],
    seen: set[str],
    pid: str,
    text: str,
    *,
    max_prompts: int,
    sub_sector: str = "general",
) -> None:
    if len(out) >= max_prompts:
        return
    text = sanitize_discovery_query(text)
    if not text or is_listicle_discovery_query(text):
        return
    key = " ".join(text.lower().split())
    if key in seen:
        return
    seen.add(key)
    out.append({"id": pid, "level": "volume", "text": text, "sub_sector": sub_sector})


def _pharma_diversity_prompts(g: str) -> list[tuple[str, str, str]]:
    """Specific pharma buckets — company sites, not listicles (2+ per sub-sector)."""
    return [
        ("D1", _q("API manufacturers", g, "pharmaceutical", "GMP", "official", "site"), "api_manufacturers"),
        ("D1b", _q("bulk drug", "API", "producer", g, "manufacturing", "plant"), "api_manufacturers"),
        ("D2", _q("formulation", "pharmaceutical", "companies", g, "official", "website"), "manufacturers"),
        ("D2b", _q("generics", "pharmaceutical", "manufacturer", g, "formulations"), "manufacturers"),
        ("D3", _q("contract manufacturing", "pharma", g, "GMP", "CDMO"), "cdmo"),
        ("D3b", _q("CDMO", "pharma", g, "contract", "manufacturer", "official"), "cdmo"),
        ("D4", _q("pharmaceutical exporters", g, "WHO", "GMP", "certified"), "exporters"),
        ("D4b", _q("pharma", "export", "company", g, "corporate", "website"), "exporters"),
        ("D5", _q("Indian pharma company", "headquarters", "corporate", "website"), "manufacturers"),
        ("D6", _q("pharma companies", g, "subsidiaries", "corporate", "profile"), "manufacturers"),
        ("D7", _q("biotech pharmaceutical", "companies", g, "R&D", "pipeline"), "biotech"),
        ("D8", _q("authorized pharmaceutical distributor", g, "firm", "official"), "distributors"),
        ("D8b", _q("pharma", "wholesale", "distributor", g, "corporate", "site"), "distributors"),
    ]


def _cyber_diversity_prompts(g: str, topic: str) -> list[tuple[str, str, str]]:
    return [
        ("D1", _q(topic, "security vendor", g, "official", "website"), "cyber_vendors"),
        ("D2", _q("MSSP", g, topic, "managed", "security", "services"), "integrators"),
        ("D3", _q("endpoint security", "vendor", g, "enterprise"), "cyber_vendors"),
        ("D4", _q("SIEM", "solution", "provider", g, "corporate"), "cyber_vendors"),
        ("D5", _q("cybersecurity", "system integrator", g, "official", "site"), "integrators"),
        ("D6", _q("cloud security", "vendor", g, "SaaS", "company"), "cyber_vendors"),
        ("D7", _q("threat intelligence", "firm", g, "corporate", "profile"), "cyber_vendors"),
        ("D8", _q("authorized", "security", "distributor", g, "partner"), "distributors"),
    ]


def _generic_diversity_prompts(topic: str, g: str, alt: str) -> list[tuple[str, str, str]]:
    return [
        ("D1", _q(topic, "manufacturer", g, "official", "website"), "manufacturers"),
        ("D2", _q(topic, "manufacturing", "company", g, "corporate", "site"), "manufacturers"),
        ("D3", _q(topic, "supplier", g, "official", "corporate", "site"), "suppliers"),
        ("D4", _q(topic, "exporter", g, "official", "company"), "exporters"),
        ("D5", _q(topic, "company", g, "headquarters", "official", "site"), "manufacturers"),
        ("D6", _q(topic, "subsidiary", g, "corporate", "profile"), "manufacturers"),
        ("D7", _q(alt or topic, "producer", g, "plant", "facility"), "manufacturers"),
        ("D8", _q("authorized", topic, "distributor", g, "firm"), "distributors"),
        ("D9", _q(topic, "OEM", "manufacturer", g, "official"), "manufacturers"),
        ("D10", _q(topic, "vendor", g, "corporate", "website"), "general"),
    ]


def build_volume_prompts(
    market: str,
    geo: str,
    *,
    industry_terms: list[str] | None = None,
    ecosystem_functions: list[str] | None = None,
    max_prompts: int = 40,
) -> list[dict[str, str]]:
    """
  Role-specific volume prompts for broader recall without listicle query patterns.
    """
    topic = refine_search_topic(market, geo)
    g = geo_search_label(geo)
    terms = [t for t in (industry_terms or []) if t]
    if topic not in terms:
        terms.insert(0, topic)
    alt = terms[1] if len(terms) > 1 else topic
    variants = topic_variants(market, geo)
    v0 = variants[0] if variants else topic

    seen: set[str] = set()
    out: list[dict[str, str]] = []

    if ecosystem_functions:
        from vendor_intel.funnel.ecosystem_prompts import build_prompts_from_ecosystem

        for row in build_prompts_from_ecosystem(
            market,
            geo,
            ecosystem_functions,
            industry_terms=terms,
            max_prompts=min(max_prompts, len(ecosystem_functions) + 6),
        ):
            _push(
                out,
                seen,
                row["id"].replace("P", "V"),
                row["text"],
                max_prompts=max_prompts,
                sub_sector=str(row.get("sub_sector") or "general"),
            )

    if _is_pharma_market(market, terms):
        buckets = _pharma_diversity_prompts(g)
    elif _is_cyber_market(market, terms):
        buckets = _cyber_diversity_prompts(g, topic)
    elif _is_chemical_market(market, terms):
        buckets = _chemical_diversity_prompts(topic, g, alt)
    elif _is_facade_market(market, terms):
        buckets = _facade_diversity_prompts(topic, g, alt)
    elif _is_timing_market(market, terms):
        buckets = _timing_diversity_prompts(topic, g, alt)
    elif _is_cnc_sim_market(market, terms):
        buckets = _cnc_diversity_prompts(topic, g, alt)
    else:
        buckets = _generic_diversity_prompts(topic, g, alt)

    for pid, text, sector in buckets:
        _push(out, seen, pid, text, max_prompts=max_prompts, sub_sector=sector)

    # Practitioner-term queries (specific, not "X companies list")
    for i, term in enumerate(terms[1:5], 1):
        _push(out, seen, f"T{i}a", _q(term, g, "official", "website"), max_prompts=max_prompts)
        _push(out, seen, f"T{i}b", _q(term, "manufacturer", g, "corporate", "site"), max_prompts=max_prompts)

    _push(out, seen, "VX1", _q(v0, "manufacturing", g, "facility"), max_prompts=max_prompts)
    _push(out, seen, "VX2", _q(topic, "registered", "company", g, "corporate"), max_prompts=max_prompts)

    return out[:max_prompts]
