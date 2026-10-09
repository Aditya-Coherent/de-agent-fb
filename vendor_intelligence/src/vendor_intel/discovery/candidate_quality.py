"""Phase 2 candidate filtering, function tagging, and scrape priority."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from vendor_intel.config import _project_root
from vendor_intel.discovery.entity_extract import (
    is_generic_category_name,
    is_plausible_company_name,
)
from vendor_intel.models import DiscoveryHit, Entity
from vendor_intel.utils.domains import domain_from_url, normalize_name

# CHANGED: phase2 quality fix — infer company function from prompt search text
_FUNCTION_KEYWORDS: list[tuple[str, str]] = [
    (r"\bcontract\s+research\b|\bcro\b|\bclinical\s+research", "cro"),
    (r"\bcontract\s+manufactur", "contract_manufacturer"),
    (r"\boem\b", "oem_manufacturer"),
    (r"\bmanufactur", "manufacturer"),
    (r"\bdistribut", "distributor"),
    (r"\bwholesal", "wholesaler"),
    (r"\bimporter?\b|\bimport\b", "importer"),
    (r"\bexport", "exporter"),
    (r"\bretailer?\b|\bdealer\b|\bfranchise", "retailer"),
    (r"\bafter[- ]?sales\b|\bservice\s+provider", "service_provider"),
    (r"\bmanaged\s+security\b|\bmssp\b|\bmdr\b|\bsoc.as.a.service", "mssp"),
    (r"\bmanaged\s+service\b|\bmsp\b", "msp"),
    (r"\bsystem\s+integrat|\bsi\b", "system_integrator"),
    (r"\bvalue.added\s+reseller|\bvar\b", "var"),
    (r"\bconsult", "consulting"),
    (r"\bsoftware\s+vendor|\bisv\b|\bsaas\b|\bcloud\s+security", "software_vendor"),
    (r"\bvendor\b|\bsolution\s+provider", "vendor"),
    (r"\bbrand", "brand"),
    (r"\bmarket\s+share", "market_leader"),
    (r"\bcomplete\s+list", "landscape"),
]

# Name-level function inference — fallback when prompt text has no function keyword.
# Applied after prompt-based inference when result is still "unknown".
_NAME_FUNCTION_KEYWORDS: list[tuple[str, str]] = [
    (r"\blaborator", "manufacturer"),
    (r"\bcontract\s+research\b|\bclinical\s+research", "cro"),
    (r"\bmssp\b|\bmanaged\s+security", "mssp"),
    (r"\bmsp\b|\bmanaged\s+service", "msp"),
    (r"\bsystem\s+integrat", "system_integrator"),
    (r"\bvalue.added\s+reseller|\bvar\b", "var"),
    (r"\bintegrat", "system_integrator"),
    (r"\bdistribut|disti", "distributor"),
    (r"\bwholesale|\bwholesaler", "wholesaler"),
    (r"\bimport", "importer"),
    (r"\bexport", "exporter"),
    (r"\bconsult", "consulting"),
    # "Services" alone → service_provider (covers Wipro, TCS etc.)
    # Do NOT match "services pvt ltd" suffix — that's a legal suffix, not the function
    (r"\bservices\b(?!\s+(?:pvt|private|limited|ltd))", "service_provider"),
    (r"\bsolutions?\b|\bsoftware\b", "software_vendor"),
    (r"\bnetwork|\bsecuri|\bcyber|\binfosec\b|\bfirewall|\bendpoint", "vendor"),
    (r"\bcontract\s+manufactur", "contract_manufacturer"),
    (r"\bsystems?\b", "vendor"),
]

# -------------------------------------------------------------------------
# Content-based function inference (Layer 3 — most reliable signal)
# Uses actual scraped website text to classify/override name- & prompt-based tags.
# -------------------------------------------------------------------------
# Each tuple: (regex_pattern, function_code, confidence)
# Ordered by specificity — more specific patterns listed first.
_CONTENT_FUNCTION_RULES: list[tuple[str, str, float]] = [
    # Security Product Vendors FIRST — strong product signals (Check Point, Fortinet…)
    (r"\bendpoint\s+protection\s+platform\b|\b(?:epp|edr|xdr)\s+(?:platform|product)\b", "vendor", 0.94),
    (r"\bfirewall\s+(?:appliance|hardware|product)\b|\bngfw\b|\bnext[\s-]gen(?:eration)?\s+firewall\b", "vendor", 0.93),
    (r"\b(?:download|buy|purchase)\s+(?:our\s+)?(?:software|product|license)\b", "vendor", 0.90),
    (r"\bsecurity\s+(?:appliance|hardware)\s+(?:product|solution)\b", "vendor", 0.90),
    (r"\bwe\s+(?:develop|build|engineer)\s+(?:security\s+)?(?:software|products?)\b", "vendor", 0.88),
    (r"\bsecurity\s+(?:platform|product)\s+(?:vendor|company)\b|\bour\s+(?:flagship\s+)?product\b", "vendor", 0.86),
    (r"\bproprietary\s+(?:engine|algorithm|technology)\b", "vendor", 0.85),
    # Brand homepages — nav/marketing copy (Check Point, Kaspersky, etc.)
    (r"\bproducts?\b.*\b(?:security|firewall|network|endpoint)\b", "vendor", 0.78),
    (r"\benterprise\s+security\b|\bcyber\s*security\s+solutions?\b", "vendor", 0.76),
    # MSSP / MDR — strict (must be provider-of-service, not partner marketing copy)
    (r"\b(?:we\s+)?provide\s+managed\s+security\b|\bour\s+mssp\b|\bmssp\s+(?:provider|services?)\b", "mssp", 0.93),
    (r"\bmanaged\s+detection\s+and\s+response\b|\bmdr\s+(?:services?|provider)\b|\bsoc[\s-]as[\s-]a[\s-]service\b", "mssp", 0.92),
    (r"\bsecurity\s+operations\s+center\b|\b24[\s/]7\s+(?:soc|monitoring|surveillance)\b", "mssp", 0.90),
    (r"\bmanaged\s+security\s+services?\s+(?:provider|company|firm)\b", "mssp", 0.90),
    # Loose MSSP mention (partner pages) — low weight so vendors win
    (r"\bmanaged\s+security\s+(?:services?|operations?)\b", "mssp", 0.68),
    # IT Services / Outsourcing (HCL, Tech Mahindra, Wipro)
    (r"\bdigital\s+transformation\b|\bit\s+outsourc|\bmanaged\s+it\s+services?\b", "service_provider", 0.90),
    (r"\bglobal\s+(?:it|technology)\s+services?\s+company\b|\bit\s+services?\s+company\b", "service_provider", 0.88),
    (r"\bit\s+(?:consulting|services)\s+(?:company|firm)\b|\bbusiness\s+process\s+(?:outsourc|service)\b", "service_provider", 0.86),
    (r"\bcyber(?:security)?\s+services?\s+(?:division|practice|unit)\b", "service_provider", 0.84),
    # Security services / advisory (often MSSP, not pure consulting)
    (r"\b(?:vapt|penetration\s+testing|security\s+audit)\s+services?\b", "mssp", 0.88),
    (r"\brisk\s+(?:and\s+)?compliance\s+services?\b|\bcyber\s+risk\s+(?:management|advisory)\b", "mssp", 0.86),
    (r"\bcyber(?:security)?\s+(?:consulting|advisory)\s+services?\b", "mssp", 0.84),
    # Pure consulting (lower priority than MSSP when both match)
    (r"\b(?:risk\s+assessment|penetration\s+testing|vapt|red\s+team)\b", "consulting", 0.82),
    (r"\bcompliance\s+(?:consulting|advisory)\b", "consulting", 0.80),
    # Distributor (Satcom Infotech, CyberDisti, BD Software)
    (r"\bauthorized\s+(?:distributor|distribution)\b|\bdistribution\s+(?:network|partner|arm)\b", "distributor", 0.92),
    (r"\bstockist\b|\bsub[\s-]?distributor\b|\bdealer\s+network\b", "distributor", 0.88),
    (r"\bwe\s+(?:distribute|supply)\s+(?:products|solutions)\b", "distributor", 0.85),
    # VAR / Reseller (Satcom Infotech also does this)
    (r"\bvalue[\s-]added\s+reseller\b|\bvar\b", "var", 0.92),
    (r"\b(?:certified|authorised|authorized)\s+(?:reseller|partner)\b", "var", 0.85),
    (r"\bimplementation\s+(?:partner|services?)\b|\bpre[\s-]?sales\s+(?:support|team)\b", "var", 0.78),
    # System Integrator
    (r"\bsystem[\s-]?integrat(?:or|ion|ing)\b|\btechnology\s+integrat\b", "system_integrator", 0.90),
    (r"\bend[\s-]to[\s-]end\s+(?:implementation|deployment|integration)\b", "system_integrator", 0.82),
    # --- Pharma (domain-agnostic) ---
    (r"\bcontract\s+research\s+organization\b|\bclinical\s+research\s+services?\b", "cro", 0.90),
    (r"\bcro\s+services?\b|\bdrug\s+discovery\s+services?\b", "cro", 0.88),
    (r"\bapi\s+manufactur|\bactive\s+pharmaceutical\s+ingredient\b", "manufacturer", 0.90),
    (r"\blaborator(?:y|ies)\b.*\bapi\b|\bapi\b.*\blaborator", "manufacturer", 0.92),
    (r"\bformulation(?:s)?\s+(?:manufactur|plant|facility)\b|\bfinished\s+dosage\b", "manufacturer", 0.88),
    (r"\bcdmo\b|\bcontract\s+(?:development|manufacturing)\b|\bcmo\b", "contract_manufacturer", 0.90),
    (r"\bpcd\s+pharma|\bpharma\s+franchise\b|\bthird\s+party\s+manufacturing\b", "contract_manufacturer", 0.85),
    (r"\bc\s*&\s*f\s+agent|\bcarrying\s+and\s+forwarding\b|\bpharma\s+distribut", "distributor", 0.88),
    # --- Food / FMCG ---
    (r"\bfmcg\b|\bpackaged\s+foods?\b|\bfood\s+processing\s+plant\b", "manufacturer", 0.88),
    (r"\bco[\s-]?packer|\bcontract\s+packag|\bprivate\s+label\s+food\b", "contract_manufacturer", 0.88),
    (r"\bmodern\s+trade\b|\bsupermarket\s+supply\b|\bfood\s+wholesal", "wholesaler", 0.85),
    # --- Generic supply chain (all industries) ---
    (r"\bmanufacturing\s+plant\b|\bproduction\s+facility\b|\bwe\s+manufacture\b", "manufacturer", 0.88),
    (r"\bwholesal(?:e|er)\b|\bbulk\s+supplier\b", "wholesaler", 0.85),
    (r"\bretail\s+chain\b|\bretailer\b|\bdealer\s+network\b", "retailer", 0.82),
    (r"\bimporter\b|\bimport(?:ing|s)?\s+(?:from|of)\b", "importer", 0.85),
    (r"\bexporter\b|\bexport(?:ing|s)?\s+(?:to|of)\b", "exporter", 0.85),
    (r"\boem\b|\boriginal\s+equipment\s+manufactur", "oem_manufacturer", 0.88),
]

def _known_entity_hint(
    company_name: str,
    domain: str,
) -> tuple[str, list[str], float] | None:
    """LLM seed list + generic name/domain patterns only (no industry-specific lists)."""
    from vendor_intel.discovery.company_registry import seed_function_for_name

    name = (company_name or "").strip()
    dom = (domain or "").lower().removeprefix("www.").split("/")[0]
    seed_fn = seed_function_for_name(name)
    if seed_fn:
        return seed_fn, [seed_fn], 0.92
    if re.search(r"\blaborator", name, re.I):
        return "manufacturer", ["manufacturer"], 0.9
    if re.search(r"\bcontract\s+research\b", name, re.I):
        return "cro", ["cro"], 0.88
    if re.search(r"\b(?:disti|distribution)\b", name, re.I) or "disti" in dom:
        return "distributor", ["distributor"], 0.85
    return None


def _resolve_primary_function(
    matched: dict[str, float],
    *,
    company_name: str = "",
    domain: str = "",
    text_len: int = 0,
) -> tuple[str, list[str], float]:
    """
    Pick primary role when multiple patterns match — avoids Check Point→MSSP,
    HCL→Consulting, Cyraacs→Consulting-only mistakes.
    """
    name = (company_name or "").strip()
    dom = (domain or "").lower().removeprefix("www.").split("/")[0]

    # IMPORTANT: known brands first — homepages often lack regex keywords (Kaspersky, Fortinet…)
    hint = _known_entity_hint(name, dom)
    if hint and not matched:
        return hint
    if not matched:
        return "unclear", ["unclear"], 0.0

    max_conf = max(matched.values())

    # Weak evidence → unclear only for unknown small companies (e.g. Velox)
    if hint is None and text_len < 350 and max_conf < 0.85:
        return "unclear", ["unclear"], max_conf * 0.5
    if hint is None and max_conf < 0.72:
        return "unclear", ["unclear"], max_conf

    def _at(fn: str) -> float:
        return matched.get(fn, 0.0)

    ranked = sorted(matched.items(), key=lambda x: -x[1])

    # Re-check hints when patterns are weak but company is well-known
    if hint:
        primary, fns, hconf = hint
        if primary == "manufacturer":
            return "manufacturer", ["manufacturer"], max(_at("manufacturer"), hconf)
        if primary == "cro":
            return "cro", ["cro"], max(_at("cro"), hconf)
        if primary == "vendor" and _at("mssp") <= 0.72:
            return "vendor", ["vendor"], max(_at("vendor"), hconf)
        if primary == "service_provider":
            fns = ["service_provider"]
            if _at("mssp") >= 0.75:
                fns.append("mssp")
            return "service_provider", fns, max(_at("service_provider"), hconf)
        if primary == "mssp":
            fns = ["mssp"]
            if _at("consulting") >= 0.78:
                fns.append("consulting")
            return "mssp", fns, max(_at("mssp"), hconf)
        if primary == "distributor":
            fns = ["distributor"]
            if _at("var") >= 0.75:
                fns.append("var")
            return "distributor", fns, max(_at("distributor"), hconf)

    # Product vendor beats loose MSSP signal (partner-page noise)
    if _at("vendor") >= 0.86 and _at("mssp") <= 0.72:
        return "vendor", ["vendor"], _at("vendor")
    if _at("vendor") >= 0.90 and _at("mssp") >= 0.70:
        return "vendor", ["vendor"], _at("vendor")

    # 5) MSSP beats consulting when both (Factosecure, Cyraacs)
    if _at("mssp") >= 0.84 and _at("consulting") >= 0.78:
        fns = ["mssp"]
        if _at("consulting") >= 0.80:
            fns.append("consulting")
        return "mssp", fns, _at("mssp")

    # 6) service_provider beats consulting for large IT (HCL without name match edge case)
    if _at("service_provider") >= 0.86 and _at("consulting") >= 0.80:
        fns = ["service_provider"]
        if _at("mssp") >= 0.75:
            fns.append("mssp")
        return "service_provider", fns, _at("service_provider")

    # 7) Default: highest confidence + multi-label ≥0.75
    best_fn, best_conf = ranked[0]
    all_fns = [fn for fn, c in ranked if c >= 0.75][:3]
    if not all_fns:
        all_fns = [best_fn]
    return best_fn, all_fns, best_conf


def infer_function_from_content(
    text: str,
    current_function: str = "unknown",
    *,
    min_confidence: float = 0.82,
    company_name: str = "",
    domain: str = "",
) -> tuple[str, list[str], float]:
    """Layer 3: classify from website text with role-priority resolution.

    Pass company_name and domain for known-vendor / IT-services overrides.
    Returns unclear when evidence is too thin to label confidently.
    """
    blob = (text or "").strip()
    name = (company_name or "").strip()
    dom = (domain or "").lower().removeprefix("www.").split("/")[0]

    # Very thin text — use LLM seed / generic name hints only
    if len(blob) < 80:
        thin_hint = _known_entity_hint(name, dom)
        if thin_hint:
            return thin_hint
        # Short vague text (Velox etc.) — never guess vendor/manufacturer
        if len(blob) < 60 or not re.search(
            r"\b(?:security|cyber|firewall|endpoint|mssp|distribut|manufactur|consult|pharma|food)\b",
            blob,
            re.I,
        ):
            return "unclear", ["unclear"], 0.0
        cur = [current_function] if current_function not in ("unknown", "") else []
        return current_function, cur, 0.0

    if len(blob) < 150:
        short_hint = _known_entity_hint(name, dom)
        if short_hint:
            return short_hint
        # Generic short text (e.g. Velox) — not enough to label
        if not re.search(
            r"\b(?:security|cyber|firewall|endpoint|mssp|distribut|manufactur|consult)\b",
            blob,
            re.I,
        ):
            return "unclear", ["unclear"], 0.25

    low = blob.lower()
    matched: dict[str, float] = {}
    for pattern, fn, conf in _CONTENT_FUNCTION_RULES:
        if re.search(pattern, low, re.I):
            if conf > matched.get(fn, 0.0):
                matched[fn] = conf

    primary, all_fns, conf = _resolve_primary_function(
        matched,
        company_name=company_name,
        domain=domain,
        text_len=len(blob),
    )

    if primary == "unclear":
        # Keep prompt/name tag (e.g. distributor) when content had no regex hits
        if current_function not in ("unknown", "", "unclear"):
            cur = [current_function]
            if all_fns and all_fns != ["unclear"]:
                cur = list(dict.fromkeys([current_function, *all_fns]))
            return current_function, cur, conf
        return "unclear", ["unclear"], conf

    if conf >= min_confidence:
        return primary, all_fns, conf

    # Below threshold: keep prior tag but attach secondary labels
    cur_list = [current_function] if current_function not in ("unknown", "") else []
    extras = [fn for fn in all_fns if fn != current_function and fn != "unclear"]
    return current_function, cur_list + extras, conf

# CHANGED: phase2 quality fix — default map when prompt text unavailable
_DEFAULT_PROMPT_FUNCTION: dict[str, str] = {
    "L0": "unknown",
    "L1": "manufacturer",
    "L2": "market_leader",
    "P1": "brand",
    "P2": "oem_manufacturer",
    "P3": "contract_manufacturer",
    "P4": "distributor",
    "P5": "retailer",
    "P6": "importer",
    "P7": "wholesaler",
    "P8": "service_provider",
    "P9": "landscape",
}


@lru_cache(maxsize=1)
def _load_junk_config() -> dict[str, Any]:
    path = _project_root() / "config" / "default.yaml"
    if not path.is_file():
        return {}
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("junk_filters") or {}


def global_junk_negative_keywords() -> list[str]:
    """CHANGED: phase2 quality fix — global junk terms merged with scope negatives."""
    cfg = _load_junk_config()
    out: list[str] = []
    for key in ("generic_names", "months", "search_platforms", "ecommerce_platforms"):
        for item in cfg.get(key) or []:
            s = str(item).strip().lower()
            if s and s not in out:
                out.append(s)
    return out


def global_blocked_domain_fragments() -> list[str]:
    cfg = _load_junk_config()
    return [str(x).strip().lower() for x in (cfg.get("media_domains") or []) if str(x).strip()]


def global_blocked_name_exact() -> frozenset[str]:
    cfg = _load_junk_config()
    exact = {str(x).strip().lower() for x in (cfg.get("blocked_names") or []) if str(x).strip()}
    return frozenset(exact)


def infer_function_from_prompt_text(text: str) -> str:
    """CHANGED: phase2 quality fix — derive function type from prompt keywords."""
    low = (text or "").lower()
    for pattern, func in _FUNCTION_KEYWORDS:
        if re.search(pattern, low):
            return func
    return "unknown"


def infer_function_from_name(name: str) -> str:
    """Fallback: infer company function from the canonical company name itself."""
    if is_generic_category_name(name):
        return "unknown"
    low = (name or "").lower()
    if re.search(r"\blaborator", low):
        return "manufacturer"
    if re.search(r"\bcontract\s+research\b", low):
        return "cro"
    for pattern, func in _NAME_FUNCTION_KEYWORDS:
        if re.search(pattern, low):
            return func
    return "vendor"  # Default: assume product/service vendor, not manufacturer


def build_prompt_function_map(prompts: list[dict[str, str]]) -> dict[str, str]:
    """Map prompt id → function type from Phase 1 discovery_prompts text."""
    mapping: dict[str, str] = dict(_DEFAULT_PROMPT_FUNCTION)
    for p in prompts or []:
        pid = str(p.get("id") or "").strip().upper()
        text = str(p.get("text") or "").strip()
        if not pid:
            continue
        fn = infer_function_from_prompt_text(text)
        if fn != "unknown" or pid not in mapping:
            mapping[pid] = fn
    return mapping


def function_for_prompt_id(prompt_id: str, prompt_map: dict[str, str]) -> str:
    pid = (prompt_id or "").strip().upper()
    if pid in prompt_map:
        return prompt_map[pid]
    return _DEFAULT_PROMPT_FUNCTION.get(pid, "unknown")


def functions_from_hits(
    hits: list[DiscoveryHit],
    prompt_map: dict[str, str],
    *,
    entity_name: str = "",
) -> tuple[str, list[str]]:
    """
    CHANGED: phase2 quality fix — primary function + all functions seen across hits.
    Falls back to name-based inference when all prompts produce 'unknown'.
    """
    counts: dict[str, int] = {}
    for h in hits:
        fn = function_for_prompt_id(h.prompt_id, prompt_map)
        if not fn or fn == "unknown":
            fn = infer_function_from_prompt_text(h.search_theme or "")
        if fn == "unknown":
            continue
        counts[fn] = counts.get(fn, 0) + 1
    if not counts:
        # No prompt-level signal — infer from the company name itself
        name_fn = infer_function_from_name(entity_name) if entity_name else "vendor"
        return name_fn, [name_fn]
    ordered = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    discovered = [fn for fn, _ in ordered]
    return discovered[0], discovered


def merge_scope_with_global_junk(scope: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize scope for Phase 2+ without polluting LLM industry negatives.

    Global junk tokens (company, market, phone, …) stay in default.yaml for
    is_junk_candidate_name() only — not merged into negative_keywords, which
    would drop real vendors via substring matches on names/domains.
    """
    merged = dict(scope or {})
    merged["negative_keywords"] = [
        str(k).strip().lower()
        for k in (merged.get("negative_keywords") or [])
        if k and str(k).strip()
    ]
    return merged


def is_junk_media_domain(domain: str) -> bool:
    """CHANGED: phase2 quality fix — drop news/review/blog domains before scrape."""
    if not domain:
        return False
    low = domain.lower().strip()
    for frag in global_blocked_domain_fragments():
        if frag in low or low == frag or low.endswith("." + frag):
            return True
    return False


def _domain_is_name_acronym(name: str, domain: str) -> bool:
    """True when the domain brand is the company's initials — a legitimate acronym domain like
    'Becton Dickinson' -> bd.com or 'GN Hearing' -> gn.com. Used to stop the name/domain-mismatch
    and non-product-site heuristics from vetoing a real company that simply uses a short acronym
    host (these majors were being dropped in phase 2 as 'junk')."""
    brand = (domain or "").strip().lower().split("/")[0].split(".")[0].replace("-", "")
    if not brand or len(brand) > 5:
        return False
    tokens = re.findall(r"[a-z0-9]+", normalize_name(name).lower())
    if len(tokens) < 2:
        return False
    _stop = {"and", "the", "of", "co", "inc", "ltd", "corp", "llc", "group", "company", "de", "la"}
    initials_all = "".join(t[0] for t in tokens if t)
    initials_core = "".join(t[0] for t in tokens if t and t not in _stop)
    return brand in (initials_all, initials_core)


def is_junk_candidate_name(name: str, domain: str = "") -> bool:
    """CHANGED: phase2 quality fix — generic words, months, platforms, media names."""
    from vendor_intel.discovery.entity_extract import (
        is_generic_category_name,
        is_generic_phrase_name,
    )
    from vendor_intel.validation.site_kind import (
        is_article_title_name,
        is_non_product_site,
        name_domain_mismatch,
    )

    low = normalize_name(name).lower().strip()
    if not low:
        return True
    if is_article_title_name(name):
        return True
    if is_generic_phrase_name(name):
        return True
    if is_generic_category_name(name):
        return True
    # A short ACRONYM host (Becton Dickinson -> bd.com) is legitimate — don't let the
    # name/domain-mismatch and non-product-site heuristics veto a real company on it.
    acronym_ok = bool(domain) and _domain_is_name_acronym(name, domain)
    if domain and not acronym_ok and is_non_product_site(domain, name=name):
        return True
    if domain and not acronym_ok and name_domain_mismatch(name, domain):
        return True
    if low in global_blocked_name_exact():
        return True
    if low in global_junk_negative_keywords():
        return True
    # Single generic industry word
    if len(low.split()) == 1 and low in global_junk_negative_keywords():
        return True
    if re.fullmatch(r"(?:january|february|march|april|may|june|july|august|"
                    r"september|october|november|december|20\d{2})", low):
        return True
    if domain and is_junk_media_domain(domain):
        return True
    # Name looks like a media site brand extracted from domain
    dom_brand = (domain or "").split(".")[0].lower()
    if dom_brand and low.replace(" ", "") == dom_brand.replace("-", ""):
        if is_junk_media_domain(domain):
            return True
    return False


def filter_junk_entities(
    entities: list[Entity],
    hits: list[DiscoveryHit],
    scope: dict[str, Any],
) -> list[Entity]:
    """CHANGED: phase2 quality fix — remove junk before scrape; runs after scope negatives."""
    from vendor_intel.discovery.company_registry import (
        filter_scope_mismatches,
        is_registry_company,
    )
    from vendor_intel.discovery.entity_extract import has_valid_candidate_domain, is_valid_company
    from vendor_intel.discovery.entity_scoring import (
        is_bad_phrase,
        passes_entity_keep_score,
        score_entity_candidate,
    )

    scope = merge_scope_with_global_junk(scope)
    entities = filter_scope_mismatches(entities, scope)

    from vendor_intel.utils.domains import company_dedupe_key

    kept: list[Entity] = []
    dropped = 0
    dropped_names: list[str] = []
    for ent in entities:
        dom = ent.primary_domain or ""
        if not dom:
            ent_key = company_dedupe_key(ent.canonical_name)
            for h in hits:
                if company_dedupe_key(h.name_raw) == ent_key or (
                    normalize_name(h.name_raw).lower()
                    == normalize_name(ent.canonical_name).lower()
                ):
                    dom = h.source_domain or domain_from_url(h.source_url) or ""
                    if dom and has_valid_candidate_domain(dom):
                        ent.primary_domain = dom
                        break
        if is_junk_candidate_name(ent.canonical_name, dom):
            dropped += 1
            dropped_names.append(ent.canonical_name)
            continue
        if is_bad_phrase(ent.canonical_name):
            dropped += 1
            dropped_names.append(ent.canonical_name)
            continue
        if dom and not has_valid_candidate_domain(dom):
            dropped += 1
            dropped_names.append(ent.canonical_name)
            continue
        if not dom and not is_registry_company(ent.canonical_name, scope):
            dropped += 1
            dropped_names.append(ent.canonical_name)
            continue
        # Phase 2 only needs basic plausibility — is_validation_ready_name is Phase 3
        # strict logic that incorrectly drops distributors, service providers, VARs, etc.
        if not is_plausible_company_name(ent.canonical_name, dom):
            dropped += 1
            dropped_names.append(ent.canonical_name)
            continue
        if not is_valid_company(ent.canonical_name, dom, scope=scope):
            dropped += 1
            dropped_names.append(ent.canonical_name)
            continue
        escore = score_entity_candidate(
            ent.canonical_name,
            dom,
            occurrence_count=int(ent.discovery_count or 1),
            scope=scope,
        )
        if not passes_entity_keep_score(escore, discovery_count=int(ent.discovery_count or 1)):
            dropped += 1
            dropped_names.append(ent.canonical_name)
            continue
        kept.append(ent)
    if dropped:
        sample = ", ".join(dropped_names[:4])
        extra = f" (+{len(dropped_names) - 4} more)" if len(dropped_names) > 4 else ""
        print(
            f"  [phase2] Dropped {dropped} junk company name(s) "
            f"(listicle/generic/media - not duplicate queries): {sample}{extra}",
            flush=True,
        )
    return kept


def scrape_priority_key(entity: Entity, *, company_function: str = "unknown") -> tuple:
    """CHANGED: phase2 quality fix — higher hits and known function scrape first."""
    return (
        -int(entity.discovery_count or 0),
        1 if (company_function or "unknown") == "unknown" else 0,
        entity.canonical_name.lower(),
    )
