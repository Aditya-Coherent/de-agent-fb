"""Post-classification quality gate — export only market participants."""
from __future__ import annotations

import json
import re
from typing import Any

from vendor_intel.discovery.company_registry import is_registry_company
from vendor_intel.intelligence.classifier import (
    _CHEM_COMPANY_HINT,
    _looks_like_industrial_company,
)
from vendor_intel.pipeline.entity_gate import reject_reason
from vendor_intel.pipeline.csv_fields import is_weak_role_description
from vendor_intel.pipeline.market_relevance import (
    keyword_profile,
    market_relevance_score,
    non_vendor_reject_reason,
    strict_market_product_match,
)
from vendor_intel.pipeline.participant_domains import (
    filter_industry_match_terms,
    is_market_research_entity,
    is_marketplace_domain,
)
from vendor_intel.utils.domains import company_dedupe_key, domain_from_url

_PHARMA_OFF_TOPIC = re.compile(
    r"\b(?:pharma|pharmaceutical|cdmo|formulation|dosage|gmp\s+certif|"
    r"firstwordpharma|emergobyul|mai-cdmo|mrchub|markspark)\b",
    re.I,
)

_CONSULTING_ONLY = re.compile(
    r"\b(?:consulting|advisory|market\s+research|intelligence\s+platform|"
    r"valuespectrum|sgsystems)\b",
    re.I,
)

_FACADE_MARKET = re.compile(
    r"\b(?:cladding|facade|façade|curtain\s*wall|rainscreen|"
    r"composite\s+panel|building\s+envelope|acp)\b",
    re.I,
)


def _market_terms(query_context: dict[str, Any], scope: dict[str, Any] | None) -> list[str]:
    terms: list[str] = []
    for key in ("industry",):
        raw = str(query_context.get(key) or "")
        terms.extend(re.findall(r"[a-z]{4,}", raw.lower()))
    if scope:
        for field in ("relevance_keywords", "industry_terms"):
            for t in scope.get(field) or []:
                s = str(t).strip().lower()
                if len(s) >= 4:
                    terms.append(s)
    seen: set[str] = set()
    out: list[str] = []
    for t in filter_industry_match_terms(terms):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:24]


_EXPORT_ROLES = frozenset(
    {
        "Manufacturer",
        "Supplier",
        "Distributor",
        "Technology Provider",
        "Integrator",
        "EPC / Engineering",
        "Project Developer",
        "Industry Body",
        "Other",
    }
)


def _role_from_discovery_function(fn: str) -> str | None:
    """Map Phase 1/2 company_function to export role."""
    low = (fn or "").strip().lower()
    if not low or low == "unknown":
        return None
    if "distribut" in low or "wholesal" in low:
        return "Distributor"
    if any(x in low for x in ("export", "import", "trad", "supplier", "feedstock")):
        return "Supplier"
    if "technology" in low or "tech provider" in low:
        return "Technology Provider"
    if "integrat" in low:
        return "Integrator"
    if any(x in low for x in ("epc", "engineering", "plant design")):
        return "EPC / Engineering"
    if "project develop" in low or "developer" in low:
        return "Project Developer"
    if "research" in low or "consult" in low:
        return "Other"
    if any(
        x in low
        for x in ("manufactur", "producer", "plant", "operator", "oem")
    ):
        return "Manufacturer"
    return None


def normalize_export_role(role: str, company_function: str = "") -> str:
    mapped = _role_from_discovery_function(company_function)
    if mapped:
        return mapped
    r = (role or "").strip()
    if r in _EXPORT_ROLES:
        return r
    if r in ("Research / Consulting", "Industry Body", ""):
        return "Other" if r != "Industry Body" else "Industry Body"
    return "Other"


def _blob(verdict: dict[str, Any], smart_data: dict[str, Any]) -> str:
    parts = [
        str(verdict.get("company") or ""),
        str(verdict.get("domain") or ""),
        json.dumps(verdict.get("signals") or {}),
        json.dumps(smart_data or {}),
    ]
    return " ".join(parts).lower()


def _has_usable_enrichment(smart_data: dict[str, Any]) -> bool:
    if not smart_data or smart_data.get("error") == "no_crawl":
        return False
    err = str(smart_data.get("error") or "")
    if err in ("thin_content", "ssc_failed") and not smart_data.get("pages"):
        return False
    pages = smart_data.get("pages") or []
    if pages and isinstance(pages[0], dict) and len(str(pages[0].get("text") or "")) >= 120:
        return True
    data = smart_data.get("data") or {}
    if isinstance(data, dict) and len(json.dumps(data)) > 100:
        return True
    return False


def _export_strictness(query_context: dict[str, Any]) -> tuple[float, float, float]:
    """Return (min_confidence, thin_crawl_conf_floor, weak_fit_floor)."""
    from vendor_intel.pipeline.geo_limits import is_global_geography

    country = str(query_context.get("country") or "global")
    if is_global_geography(country):
        return 0.45, 0.55, 0.42
    return 0.48, 0.62, 0.48


def _is_facade_market(scope: dict[str, Any] | None, query_context: dict[str, Any]) -> bool:
    if scope and scope.get("tier1_market") == "aluminium_cladding":
        return True
    blob = " ".join(
        str(query_context.get(k) or "")
        for k in ("industry", "country")
    )
    if scope:
        blob += f" {scope.get('market') or ''}"
        terms = scope.get("industry_terms") or scope.get("relevance_keywords") or []
        if isinstance(terms, list):
            blob += " " + " ".join(str(t) for t in terms)
    return bool(_FACADE_MARKET.search(blob))


def _facade_participant_signals(sig: dict[str, Any], text: str) -> bool:
    if not sig.get("has_products"):
        return False
    if sig.get("is_manufacturer") or sig.get("is_supplier") or sig.get("is_distributor"):
        return True
    return bool(
        re.search(
            r"\b(?:acp|rainscreen|curtain\s*wall|cladding\s+system|facade\s+panel|"
            r"composite\s+panel|ventilated\s+facade)\b",
            text,
            re.I,
        )
    )


def _is_contractor_lane(verdict: dict[str, Any]) -> bool:
    fn = str(
        verdict.get("company_function")
        or verdict.get("discovered_via_function")
        or ""
    ).lower()
    role_desc = str(verdict.get("role_description") or "").lower()
    sub = str(verdict.get("discovery_sub_sector") or "").lower()
    if sub == "contractors" or "contractor" in fn:
        return True
    if "contractor" in role_desc and "manufacturer" not in role_desc:
        return True
    if fn in ("installer", "integrator") and any(
        x in role_desc for x in ("install", "contractor", "erect", "fit-out")
    ):
        return True
    return False


def _is_vip_participant(
    name: str,
    domain: str,
    *,
    is_seed: bool,
    scope: dict[str, Any] | None,
) -> bool:
    return is_seed or is_registry_company(name, scope)


def score_export_row(
    verdict: dict[str, Any],
    smart_data: dict[str, Any],
    query_context: dict[str, Any],
    *,
    scope: dict[str, Any] | None = None,
    is_seed: bool = False,
) -> tuple[float, str]:
    """
    Return (quality_score 0-1, reject_reason or '').
    """
    name = str(verdict.get("company") or "")
    domain = str(verdict.get("domain") or "")
    text = _blob(verdict, smart_data)
    is_vip = _is_vip_participant(name, domain, is_seed=is_seed, scope=scope)

    if is_market_research_entity(name, domain) or is_marketplace_domain(domain):
        return 0.0, "market_research_site"

    sig = verdict.get("signals") or {}
    facade_market = _is_facade_market(scope, query_context)
    facade_participant = facade_market and _facade_participant_signals(sig, text)

    conf = float(verdict.get("confidence") or 0)
    role_early = normalize_export_role(
        str(verdict.get("role") or ""),
        str(verdict.get("company_function") or verdict.get("discovered_via_function") or ""),
    )
    trust_llm = bool(
        verdict.get("is_relevant")
        and conf >= 0.55
        and role_early not in ("Other", "Research / Consulting", "Industry Body")
    )

    if not is_vip:
        gate = reject_reason(name, domain, text=text)
        if gate in ("non_product_site", "site_kind_directory", "site_kind_aggregator"):
            _sec = str(verdict.get("value_chain_section") or "").strip().lower()
            _inscope = bool(_sec) and _sec not in ("other", "unknown", "n/a", "none", "uncategorized")
            # Operators/service providers sell capacity/software, not a "product" — don't let the
            # non-product heuristic drop one the LLM judged relevant AND slotted into a real section.
            if (facade_participant and verdict.get("is_relevant")) or trust_llm or (
                verdict.get("is_relevant") and _inscope
            ):
                gate = None
        if gate:
            return 0.0, gate

    min_conf, thin_floor, weak_floor = _export_strictness(query_context)
    usable = _has_usable_enrichment(smart_data)
    market_terms = _market_terms(query_context, scope)
    market_hit = bool(sig.get("industry_match")) or any(
        t in text for t in market_terms if len(t) >= 5
    )
    profile = keyword_profile(scope, query_context)
    mscore = int(verdict.get("market_relevance_score") or 0)
    if mscore == 0:
        mscore = market_relevance_score(text, profile, domain=domain, name=name)
    role_desc = str(verdict.get("role_description") or "")

    # Classifier said not relevant — never export (seeds are bumped to relevant in filter_for_export).
    if not verdict.get("is_relevant"):
        return 0.0, "not_relevant"

    if not is_vip and role_early == "Other" and conf < 0.68:
        if not (mscore >= 1 or verdict.get("landscape_strengthened") or verdict.get("llm_inmarket_trusted")):
            return 0.0, "weak_other_role"

    nv_reason = non_vendor_reject_reason(
        name,
        domain,
        text,
        scope=scope,
        query_context=query_context,
        trust_classify=trust_llm,
    )
    if nv_reason and not is_vip:
        return 0.0, nv_reason

    if profile.strict_product_gate and not is_vip and not market_hit:
        if not strict_market_product_match(text, query_context, scope):
            if not (
                mscore >= 1
                or verdict.get("landscape_strengthened")
                or verdict.get("llm_inmarket_trusted")
                or (
                    conf >= 0.55
                    and usable
                    and role_early not in ("Other", "Research / Consulting")
                )
            ):
                return 0.0, "weak_product_fit"
    if facade_participant and conf >= min_conf - 0.04:
        thin_floor = max(0.62, thin_floor - 0.06)
    if not is_vip and conf < min_conf:
        return 0.0, "low_confidence"
    industry = str(query_context.get("industry") or "").lower()

    if (
        _PHARMA_OFF_TOPIC.search(text)
        and "pharma" not in industry
        and "pharmaceutical" not in industry
        and not is_vip
    ):
        if _CHEM_COMPANY_HINT.search(f"{name} {domain}"):
            if len(_PHARMA_OFF_TOPIC.findall(text)) < 2:
                pass
            else:
                return 0.0, "off_topic_pharma"
        else:
            return 0.0, "off_topic_pharma"

    if _CONSULTING_ONLY.search(text) and not is_vip:
        return 0.0, "consulting_not_participant"

    role = normalize_export_role(
        str(verdict.get("role") or ""),
        str(verdict.get("company_function") or verdict.get("discovered_via_function") or ""),
    )

    if not usable and not is_vip and conf < thin_floor:
        return 0.0, "no_usable_website_content"

    score = conf * 0.45
    if is_vip and verdict.get("is_relevant"):
        score = max(score, 0.78)
    if market_hit:
        score += 0.22
    if mscore >= 2:
        score += 0.12
    elif mscore >= 1:
        score += 0.05
    if verdict.get("landscape_strengthened"):
        score += 0.1
    if is_weak_role_description(role_desc) and not verdict.get("landscape_strengthened"):
        score = max(0.0, score - 0.06)
    if usable:
        score += 0.18
    if sig.get("is_manufacturer"):
        score += 0.12
    if sig.get("is_supplier") or sig.get("is_distributor"):
        score += 0.14
    if role in _EXPORT_ROLES and role != "Other":
        score += 0.05
    if role == "Industry Body":
        score = min(score, 0.55)

    if _is_contractor_lane(verdict) and not is_vip:
        score = max(0.0, score - 0.14)

    if score < weak_floor and not market_hit and not (
        mscore >= 1
        or verdict.get("landscape_strengthened")
        or sig.get("is_manufacturer")
        or sig.get("is_supplier")
        or sig.get("is_distributor")
    ):
        return score, "weak_market_fit"

    return min(1.0, score), ""


def _export_domain(row: dict[str, Any]) -> str:
    raw = str(row.get("domain") or row.get("website") or "").strip()
    if not raw:
        return ""
    if "://" in raw or raw.startswith("www."):
        return domain_from_url(raw if "://" in raw else f"https://{raw}")
    dom = raw.lower()
    if dom.startswith("www."):
        dom = dom[4:]
    return dom.split("/")[0].split("?")[0].strip(".")


def dedupe_export_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one row per domain and per normalized company name (highest quality wins)."""
    ranked = sorted(
        rows,
        key=lambda r: float(r.get("quality_score") or r.get("confidence") or 0),
        reverse=True,
    )
    seen_dom: set[str] = set()
    seen_name: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in ranked:
        dom = _export_domain(row)
        name_key = company_dedupe_key(str(row.get("company") or ""))
        if dom and dom in seen_dom:
            continue
        if name_key and name_key in seen_name:
            continue
        if dom:
            seen_dom.add(dom)
        if name_key:
            seen_name.add(name_key)
        out.append(row)
    return out


def filter_for_export(
    classified: list[dict[str, Any]],
    enriched: dict[str, Any],
    query_context: dict[str, Any],
    *,
    scope: dict[str, Any] | None = None,
    seed_domains: set[str] | None = None,
    min_rows: int = 0,
    max_rows: int = 65,
    min_quality: float = 0.55,
    pad_to_min: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (export_rows, rejected_rows). Never pads by default — export only vetted rows."""
    seed_domains = seed_domains or set()
    seed_scored: list[tuple[float, dict[str, Any]]] = []
    scored: list[tuple[float, dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []
    seen_dom: set[str] = set()
    seen_name: set[str] = set()

    # Clear majors: companies the LLM listed in its dominant-players pass. These must never be
    # silently dropped by an over-strict relevance call (e.g. a major operator judged "not a
    # systems company"). Matched by NAME so a seed/crawl domain mismatch doesn't lose them.
    import re as _re

    def _mk(s: str) -> str:
        return _re.sub(r"[^a-z0-9]", "", str(s or "").lower())

    _major_keys = {_mk(n) for n in ((scope or {}).get("major_player_names") or []) if _mk(n)}

    def _is_major(nm: str) -> bool:
        k = _mk(nm)
        if not k or not _major_keys:
            return False
        return any(k == m or (len(k) >= 5 and k in m) or (len(m) >= 5 and m in k) for m in _major_keys)

    for v in classified:
        dom = _export_domain(v)
        name = str(v.get("company") or "")
        smart = enriched.get(name) or enriched.get(dom) or {}
        is_major = _is_major(name)
        is_seed = dom in seed_domains or bool(v.get("is_seed")) or is_major
        is_vip = _is_vip_participant(name, dom, is_seed=is_seed, scope=scope) or is_major
        if is_vip and not v.get("is_relevant") and (is_major or _looks_like_industrial_company(name, dom)):
            v = {
                **v,
                "is_relevant": True,
                "confidence": max(float(v.get("confidence") or 0), 0.70 if is_major else 0.62),
            }
        disc_fn = str(v.get("company_function") or v.get("discovered_via_function") or "")
        v = {
            **v,
            "role": normalize_export_role(str(v.get("role") or ""), disc_fn),
        }
        q, reason = score_export_row(
            v, smart, query_context, scope=scope, is_seed=is_seed
        )
        row = {**v, "quality_score": round(q, 3)}
        if reason:
            row["export_reject"] = reason
            rejected.append(row)
            continue
        if q < min_quality:
            row["export_reject"] = "below_quality_threshold"
            rejected.append(row)
            continue
        if dom in seen_dom:
            row["export_reject"] = "duplicate_domain"
            rejected.append(row)
            continue
        name_key = str(v.get("company") or "")
        dedupe_key = company_dedupe_key(name_key)
        if dedupe_key and dedupe_key in seen_name:
            row["export_reject"] = "duplicate_company"
            rejected.append(row)
            continue
        seen_dom.add(dom)
        if dedupe_key:
            seen_name.add(dedupe_key)
        if is_seed:
            seed_scored.append((q, row))
        else:
            scored.append((q, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    seed_scored.sort(key=lambda x: x[0], reverse=True)
    export = [r for _, r in seed_scored]
    remaining = max_rows - len(export)
    if remaining > 0:
        export.extend(r for _, r in scored[:remaining])

    if pad_to_min and min_rows > 0 and len(export) < min_rows:
        for q, r in scored:
            if len(export) >= min_rows:
                break
            if _export_domain(r) in {_export_domain(x) for x in export}:
                continue
            if q >= min_quality and r.get("is_relevant"):
                export.append(r)

    return dedupe_export_rows(export), rejected
