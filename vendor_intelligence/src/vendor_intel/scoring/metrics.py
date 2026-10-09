"""Grounded company scores from measurable signals (no LLM-invented numbers)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from vendor_intel.discovery.company_registry import (
    get_registry_scope,
    is_registry_company,
)
from vendor_intel.validation.scrape_signals import analyze_site_text

# Transparent weights — must sum to 1.0
WEIGHT_DISCOVERY = 0.20
WEIGHT_EVIDENCE = 0.35
WEIGHT_VERIFICATION = 0.15
WEIGHT_SCRAPE = 0.30

DISCOVERY_FULL_AT = 8  # discovery_count at which discovery_score hits 1.0
MIN_DOMAINS_FULL = 3


@dataclass
class ScoreBreakdown:
    discovery_score: float = 0.0
    evidence_score: float = 0.0
    verification_score: float = 0.0
    scrape_score: float = 0.0
    registry_bonus: float = 0.0
    gates_passed: int = 0
    gates_total: int = 4
    distinct_domains: int = 0
    composite: float = 0.0
    tier_recommendation: str = "C"
    formula: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _gate_pass_count(gate_pass: dict[str, bool]) -> int:
    keys = ("operational", "geography", "product", "activity")
    return sum(1 for k in keys if gate_pass.get(k))


def _evidence_score(entity, gate_pass: dict[str, bool]) -> tuple[float, int]:
    passed = _gate_pass_count(gate_pass)
    base = passed / 4.0
    domains = int(getattr(entity, "distinct_domains", 0) or 0)
    domain_factor = min(1.0, domains / max(MIN_DOMAINS_FULL, 1))
    # Require operational for high evidence
    if not gate_pass.get("operational"):
        base *= 0.55
    return min(1.0, base * 0.7 + domain_factor * 0.3), passed


def compute_score_breakdown(
    entity,
    *,
    gate_pass: dict[str, bool] | None = None,
    market: str = "",
    verification_confidence: float | None = None,
    scope: dict | None = None,
) -> ScoreBreakdown:
    """
    Composite score in [0, 1] from observable inputs only.
    LLM may suggest tier changes but should not invent this number.
    """
    scope = scope or get_registry_scope() or {}
    gp = gate_pass if gate_pass is not None else (entity.gate_pass or {})

    disc = min(1.0, int(getattr(entity, "discovery_count", 0) or 0) / DISCOVERY_FULL_AT)
    ev_score, gates_passed = _evidence_score(entity, gp)
    domains = int(getattr(entity, "distinct_domains", 0) or 0)

    text = (getattr(entity, "scraped_text", "") or getattr(entity, "company_description", "") or "")
    _scraped_urls = getattr(entity, "scraped_urls", None) or []
    _scraped_url = _scraped_urls[0] if _scraped_urls else (getattr(entity, "primary_domain", "") or "")
    sig = analyze_site_text(text, market=market, scope=scope, url=_scraped_url)
    scrape = float(sig.get("confidence") or 0.0)
    if sig.get("junk_site"):
        scrape = min(scrape, 0.1)

    verify = float(verification_confidence) if verification_confidence is not None else 0.0
    if verify <= 0 and disc >= 0.75:
        verify = 0.55  # discovery-only floor, not LLM

    reg_bonus = 0.08 if is_registry_company(entity.canonical_name, scope) else 0.0

    composite = (
        WEIGHT_DISCOVERY * disc
        + WEIGHT_EVIDENCE * ev_score
        + WEIGHT_VERIFICATION * verify
        + WEIGHT_SCRAPE * scrape
        + reg_bonus
    )

    # CHANGED: phase3 quality fix — evidence-quality modifiers (not just gate pass/fail)
    domain_bonus = min(0.15, max(0.0, (domains - 2) * 0.02))
    primary_dom = (getattr(entity, "primary_domain", "") or "").lower()
    weak_domains = (
        "linkedin", "facebook", "twitter", "wikipedia",
        "indiamart", "justdial", "tradeindia", "zauba",
    )
    domain_quality_bonus = (
        0.05
        if primary_dom and not any(x in primary_dom for x in weak_domains)
        else 0.0
    )
    text_len = len(text.strip())
    # Bonus for rich content; penalty for thin/dead pages (threshold raised to 400 chars)
    scrape_bonus = 0.10 if text_len > 2000 else (0.05 if text_len > 400 else 0.0)
    # Dead or 404 sites: zero out scrape score entirely
    if sig.get("dead_site"):
        scrape = 0.0
    # Thin content (<400 chars): apply a scrape penalty — signals weak/blocked site
    thin_scrape_penalty = 0.08 if (text_len < 400 and not sig.get("dead_site")) else 0.0
    weak_evidence_domains = {
        "linkedin.com", "facebook.com", "twitter.com", "wikipedia.org",
        "indiamart.com", "justdial.com", "tradeindia.com", "zauba.com",
        "simplywall.st", "crunchbase.com", "zaubacorp.com",
    }
    evidence_urls_all = [
        it.url for items in (getattr(entity, "gates", {}) or {}).values() for it in items
    ]
    weak_count = sum(
        1 for u in evidence_urls_all
        if any(d in (u or "").lower() for d in weak_evidence_domains)
    )
    weak_penalty = min(0.15, weak_count * 0.03)

    composite = composite + domain_bonus + domain_quality_bonus + scrape_bonus - weak_penalty - thin_scrape_penalty
    composite = min(1.0, max(0.0, composite))

    tier = _tier_from_metrics(
        composite=composite,
        gate_pass=gp,
        gates_passed=gates_passed,
        scrape=scrape,
        disc=disc,
        registry=reg_bonus > 0,
        junk=bool(sig.get("junk_site")),
        company_name=getattr(entity, "canonical_name", "") or "",
    )

    formula = (
        f"{WEIGHT_DISCOVERY}*disc({disc:.2f}) + "
        f"{WEIGHT_EVIDENCE}*ev({ev_score:.2f}) + "
        f"{WEIGHT_VERIFICATION}*ver({verify:.2f}) + "
        f"{WEIGHT_SCRAPE}*scrape({scrape:.2f})"
        + (f" + reg({reg_bonus:.2f})" if reg_bonus else "")
    )

    return ScoreBreakdown(
        discovery_score=round(disc, 3),
        evidence_score=round(ev_score, 3),
        verification_score=round(verify, 3),
        scrape_score=round(scrape, 3),
        registry_bonus=round(reg_bonus, 3),
        gates_passed=gates_passed,
        gates_total=4,
        distinct_domains=domains,
        composite=round(composite, 3),
        tier_recommendation=tier,
        formula=formula,
    )


def _tier_from_metrics(
    *,
    composite: float,
    gate_pass: dict[str, bool],
    gates_passed: int,
    scrape: float,
    disc: float,
    registry: bool,
    junk: bool,
    company_name: str = "",
) -> str:
    from vendor_intel.discovery.entity_extract import (
        is_generic_category_name,
        is_generic_phrase_name,
        is_likely_real_company_name,
    )

    name = (company_name or "").strip()
    if is_generic_category_name(name) or is_generic_phrase_name(name):
        return "C"
    if name and not registry and not is_likely_real_company_name(name):
        return "C"
    if junk:
        return "C"
    op = bool(gate_pass.get("operational"))
    core = bool(gate_pass.get("geography") or gate_pass.get("product"))
    activity = bool(gate_pass.get("activity"))

    if not op or gates_passed < 2:
        return "C"

    # Tier A — strong evidence (activity optional in fast validation mode)
    if composite >= 0.74 and gates_passed >= 3 and (activity or disc >= 0.5) and scrape >= 0.45:
        return "A"

    # Tier A — LLM seed companies with solid geo + product gates
    if registry and composite >= 0.58 and gates_passed >= 3 and scrape >= 0.48:
        return "A"

    # Tier B — operating company with core gates and usable scrape
    if composite >= 0.42 and gates_passed >= 2 and core and scrape >= 0.28:
        return "B"
    if composite >= 0.38 and gates_passed >= 2 and (disc >= 0.15 or registry) and scrape >= 0.30:
        return "B"
    if registry and op and scrape >= 0.30 and gates_passed >= 2:
        return "B"
    # Corporate-suffix names (Laboratories, Pharma, Healthcare) with real site evidence
    name = (company_name or "").strip()
    if (
        name
        and gates_passed >= 2
        and op
        and scrape >= 0.28
        and disc >= 0.12
        and re.search(
            r"\b(?:laborator|pharmaceuticals?|pharma|healthcare|industries|limited|ltd)\b",
            name,
            re.I,
        )
    ):
        return "B"
    return "C"


def apply_metrics_to_entity(
    entity,
    *,
    gate_pass: dict[str, bool] | None = None,
    market: str = "",
    verification_confidence: float | None = None,
    scope: dict | None = None,
    respect_agent_tier: bool = True,
) -> ScoreBreakdown:
    """Set composite_score, tier (unless agent locked), and score_breakdown on entity."""
    bd = compute_score_breakdown(
        entity,
        gate_pass=gate_pass,
        market=market,
        verification_confidence=verification_confidence,
        scope=scope,
    )
    entity.composite_score = bd.composite
    entity.product_fit_score = bd.scrape_score
    entity.geo_fit_score = 1.0 if (gate_pass or entity.gate_pass or {}).get("geography") else 0.0
    if hasattr(entity, "score_breakdown"):
        entity.score_breakdown = bd.to_dict()

    agent_locked = respect_agent_tier and (
        (entity.inclusion_reason or "").startswith("agent_")
        or entity.suppression_reason == "agent_rejected"
    )
    if not agent_locked:
        prev = entity.tier
        entity.tier = bd.tier_recommendation
        if entity.tier in ("A", "B") and prev == "C":
            entity.suppression_reason = None
        if entity.tier == "C" and not entity.suppression_reason:
            entity.suppression_reason = "metrics_below_threshold"

    if not entity.inclusion_reason or entity.inclusion_reason.startswith("metrics_"):
        entity.inclusion_reason = f"metrics: {bd.formula} → {bd.composite}"

    return bd
