import re

from rapidfuzz import fuzz

from vendor_intel.discovery.company_registry import (
    boost_registry_discovery_count,
    canonical_display_name,
    enrich_entity_domain,
)
from vendor_intel.discovery.entity_scoring import finalize_entity_name, is_bad_phrase
from vendor_intel.discovery.entity_extract import (
    is_generic_category_name,
    is_plausible_company_name,
    looks_like_company_site,
    looks_like_company_structure,
    pick_primary_domain,
)
from vendor_intel.utils.domains import company_dedupe_key, domain_from_url, normalize_name
from vendor_intel.models import DiscoveryHit, Entity


def merge_discovery_hits(hits: list[DiscoveryHit]) -> dict[str, list[DiscoveryHit]]:
    """Bucket by normalized company name (not title+domain noise)."""
    buckets: dict[str, list[DiscoveryHit]] = {}
    for h in hits:
        key = company_dedupe_key(h.name_raw)
        if len(key) < 3:
            continue
        buckets.setdefault(key, []).append(h)
    return buckets


def _funnel_levels_from_group(group: list[DiscoveryHit]) -> list[str]:
    levels: list[str] = []
    for h in group:
        lv = (h.funnel_level or "").strip()
        if lv and lv not in levels:
            levels.append(lv)
    order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
    return sorted(levels, key=lambda x: order.get(x, 99))


# CHANGED: phase2 quality fix — only merge very similar names (was 96)
_NAME_MERGE_SIMILARITY = 98


def _should_merge_entities(a: Entity, b: Entity) -> bool:
    """CHANGED: phase2 quality fix — less aggressive deduplication."""
    name_a = a.canonical_name.lower()
    name_b = b.canonical_name.lower()
    dom_a = (a.primary_domain or "").lower().strip()
    dom_b = (b.primary_domain or "").lower().strip()

    # Rule 1: same primary_domain = definite duplicate
    if dom_a and dom_b and dom_a == dom_b:
        return True

    # Rule 4: short names — require domain match, not name similarity alone
    if min(len(name_a), len(name_b)) < 4:
        return False

    ratio = fuzz.ratio(name_a, name_b)
    # Rule 2: alias key (Dr. Reddy's vs Dr Reddys Laboratories)
    if company_dedupe_key(a.canonical_name) == company_dedupe_key(b.canonical_name):
        return True

    # Rule 3: high name similarity only (conservative threshold)
    if ratio >= _NAME_MERGE_SIMILARITY:
        return True

    return False


def build_entities_from_hits(hits: list[DiscoveryHit]) -> list[Entity]:
    buckets = merge_discovery_hits(hits)
    entities: list[Entity] = []
    for _key, group in buckets.items():
        raw = canonical_display_name(normalize_name(group[0].name_raw))
        if is_bad_phrase(raw):
            continue
        name = raw
        if len(name) < 3 or not is_plausible_company_name(name):
            continue
        if not looks_like_company_structure(name):
            continue
        if is_generic_category_name(name):
            continue
        domains = {h.source_domain or domain_from_url(h.source_url) for h in group}
        domains.discard("")
        primary = enrich_entity_domain(
            name,
            pick_primary_domain(group, name),
            [h.source_domain or domain_from_url(h.source_url) for h in group],
        )
        if primary:
            final = finalize_entity_name(name, primary)
            if not final:
                continue
            name = canonical_display_name(normalize_name(final))
            if not is_plausible_company_name(name, primary):
                continue
        if not primary:
            for h in group:
                dom = h.source_domain or domain_from_url(h.source_url)
                if dom and looks_like_company_site(
                    h.source_url, dom, h.name_raw or name
                ):
                    primary = dom
                    break
        entities.append(
            Entity(
                canonical_name=name,
                primary_domain=primary,
                discovery_count=boost_registry_discovery_count(name, len(group)),
                distinct_domains_discovery=len(domains),
                funnel_levels_seen=_funnel_levels_from_group(group),
            )
        )
    merged: list[Entity] = []
    used: set[int] = set()
    for i, e in enumerate(entities):
        if i in used:
            continue
        cluster = [e]
        used.add(i)
        for j, o in enumerate(entities):
            if j in used or i == j:
                continue
            if _should_merge_entities(e, o):
                cluster.append(o)
                used.add(j)
        if len(cluster) == 1:
            merged.append(e)
        else:
            def _name_quality(e: Entity) -> tuple:
                n = e.canonical_name
                score = e.discovery_count
                if re.search(r"\blaborator|pharmaceutical|limited|ltd\b", n, re.I):
                    score += 50
                if re.search(r"\b(?:the|explore|market|certified|export\s+companies)\b", n, re.I):
                    score -= 40
                if len(n.split()) <= 2 and not re.search(
                    r"\b(?:pharma|labs?|healthcare|industries)\b", n, re.I
                ):
                    score -= 20
                return (score, len(n))

            best = max(cluster, key=_name_quality)
            all_domains = {best.primary_domain} if best.primary_domain else set()
            all_levels: list[str] = []
            for c in cluster:
                if c.primary_domain:
                    all_domains.add(c.primary_domain)
                for lv in c.funnel_levels_seen:
                    if lv not in all_levels:
                        all_levels.append(lv)
            best.discovery_count = sum(c.discovery_count for c in cluster)
            best.distinct_domains_discovery = len(all_domains)
            best.funnel_levels_seen = all_levels
            best.aliases = list(
                {c.canonical_name for c in cluster if c.canonical_name != best.canonical_name}
            )
            if not best.primary_domain:
                hits_flat: list[DiscoveryHit] = []
                for c in cluster:
                    hits_flat.extend(buckets.get(normalize_name(c.canonical_name).lower(), []))
                if hits_flat:
                    best.primary_domain = pick_primary_domain(hits_flat, best.canonical_name)
            merged.append(best)
    merged.sort(key=lambda e: (-e.discovery_count, e.canonical_name))
    return merged
