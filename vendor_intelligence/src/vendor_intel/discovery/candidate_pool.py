"""Keep enough verified candidates for 40–50 solid companies in later phases."""
from __future__ import annotations

from dataclasses import dataclass

from vendor_intel.discovery.company_verify import VerificationResult
from vendor_intel.discovery.company_registry import (
    is_registry_company,
    registry_companies_for_scope,
)
from vendor_intel.discovery.entity_extract import (
    is_generic_category_name,
    is_likely_real_company_name,
    is_plausible_company_name,
)
from vendor_intel.models import Entity
from vendor_intel.utils.domains import company_dedupe_key


@dataclass
class VerificationRecord:
    verdict: str
    confidence: float
    reason: str = ""


def verification_passes(
    rec: VerificationRecord | VerificationResult | None,
    *,
    discovery_count: int = 0,
    name: str = "",
    domain: str = "",
) -> bool:
    from vendor_intel.validation.site_kind import is_non_product_site

    if name and domain and is_non_product_site(domain, name=name):
        return False
    if rec is None:
        return discovery_count >= 3
    verdict = rec.verdict
    conf = rec.confidence
    reason = getattr(rec, "reason", "") or ""
    if reason in (
        "non_product_site_domain_match",
        "non_product_site",
        "article_title_not_company",
        "name_domain_mismatch",
    ):
        return False
    if verdict == "real_company" and conf >= 0.72:
        return True
    if verdict == "real_company" and conf < 0.72:
        return discovery_count >= 3
    if verdict == "likely_real" and conf >= 0.5:
        return True
    if verdict == "unclear" and conf >= 0.42 and discovery_count >= 2:
        return True
    if verdict == "unclear" and discovery_count >= 4:
        return True
    return False


def build_verification_map(
    report: list[dict],
) -> dict[str, VerificationRecord]:
    out: dict[str, VerificationRecord] = {}
    for row in report:
        name = str(row.get("name") or "").strip().lower()
        if not name:
            continue
        out[name] = VerificationRecord(
            verdict=str(row.get("verdict") or ""),
            confidence=float(row.get("confidence") or 0),
            reason=str(row.get("reason") or ""),
        )
    return out


def _entity_passes_quality(ent: Entity, scope: dict | None) -> bool:
    dom = ent.primary_domain or ""
    if is_generic_category_name(ent.canonical_name):
        return False
    if not is_plausible_company_name(ent.canonical_name, dom):
        return False
    if not is_likely_real_company_name(ent.canonical_name, dom):
        if not is_registry_company(ent.canonical_name, scope):
            return False
    return True


def ensure_minimum_candidates(
    entities: list[Entity],
    verification_report: list[dict],
    *,
    target_solid: int = 50,
    max_pool: int = 75,
    scope: dict | None = None,
) -> tuple[list[Entity], list[str]]:
    """
    Build pool of 50–75 real companies for scrape/validation.
    Never pads with generic category names.
    """
    warnings: list[str] = []
    vmap = build_verification_map(verification_report)
    ranked = sorted(entities, key=lambda e: (-e.discovery_count, e.canonical_name))

    kept: list[Entity] = []
    seen_keys: set[str] = set()

    def _add(ent: Entity) -> bool:
        key = company_dedupe_key(ent.canonical_name)
        if key in seen_keys:
            return False
        if not _entity_passes_quality(ent, scope):
            return False
        seen_keys.add(key)
        kept.append(ent)
        return True

    for ent in ranked:
        if len(kept) >= max_pool:
            break
        rec = vmap.get(ent.canonical_name.lower())
        if verification_passes(
            rec,
            discovery_count=ent.discovery_count,
            name=ent.canonical_name,
            domain=ent.primary_domain or "",
        ):
            _add(ent)

    for display, domain, _fn in registry_companies_for_scope(scope or []):
        if len(kept) >= target_solid:
            break
        match = next(
            (
                e
                for e in entities
                if company_dedupe_key(e.canonical_name) == company_dedupe_key(display)
            ),
            None,
        )
        if match:
            if domain:
                match.primary_domain = domain
            _add(match)
        elif domain and _entity_passes_quality(
            Entity(
                canonical_name=display,
                primary_domain=domain,
                discovery_count=5,
            ),
            scope,
        ):
            kept.append(
                Entity(
                    canonical_name=display,
                    primary_domain=domain,
                    discovery_count=5,
                )
            )
            seen_keys.add(company_dedupe_key(display))

    if len(kept) < target_solid:
        for ent in ranked:
            if len(kept) >= target_solid:
                break
            rec = vmap.get(ent.canonical_name.lower())
            if rec and rec.verdict == "not_company":
                continue
            if ent.discovery_count < 2 and not is_registry_company(ent.canonical_name, scope):
                continue
            if not ent.primary_domain:
                continue
            _add(ent)
        if len(kept) < target_solid:
            warnings.append(
                f"Candidate pool {len(kept)} below target {target_solid} after quality filter; "
                "only real companies kept (no junk padding)."
            )

    kept.sort(key=lambda e: (-e.discovery_count, e.canonical_name))
    return kept[:max_pool], warnings
