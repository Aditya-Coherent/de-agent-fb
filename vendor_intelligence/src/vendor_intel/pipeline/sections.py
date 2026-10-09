"""Value-chain sectioning for the CEO landscape Excel/CSV.

Groups exported companies into value-chain buckets (Manufacturers of the main
product, Other component manufacturers, Technology providers, System integrators,
Distributors of the main product, Other component distributors, ...).

The section per company is decided by the LLM (the `value_chain_section` field on
the classifier verdict, chosen from the taxonomy we pass into the classify call)
and falls back to a rule-based map from the existing `role` when the LLM did not
return a usable label. The taxonomy itself is built once per run from the market's
main product so labels are market-specific (e.g. "Manufacturers of Battery
Management System") yet consistent across every company in the run.
"""
from __future__ import annotations

import re
from typing import Any

# Words that describe the *query wrapper* rather than the product itself.
_DROP_TAIL = re.compile(
    r"\b(markets?|industr(?:y|ies)|sectors?|landscape|companies|company|vendors?|"
    r"suppliers?|manufacturers?|space|ecosystem|segment)\b",
    re.I,
)
_DROP_GEO = re.compile(r"\b(global|worldwide|international|domestic)\b", re.I)


def _title(text: str) -> str:
    """Light title-case that preserves short all-caps tokens (BMS, OEM, EV)."""
    out: list[str] = []
    for word in text.split():
        if word.isupper() and len(word) <= 5:
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def main_product_label(
    query_context: dict[str, Any] | None, scope: dict[str, Any] | None = None
) -> str:
    """Best short name for the market's main product, for section headings."""
    raw = ""
    if isinstance(scope, dict):
        raw = str(scope.get("market") or scope.get("product_category") or "").strip()
    if not raw:
        raw = str((query_context or {}).get("industry") or "").strip()
    # Drop a trailing "in <Country>" if it slipped into the market string.
    cleaned = re.sub(r"\bin\s+[A-Z][\w .,&'-]+$", "", raw).strip()
    cleaned = _DROP_GEO.sub(" ", cleaned)
    cleaned = _DROP_TAIL.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,-")
    return _title(cleaned) if cleaned else (_title(raw) if raw else "the main product")


def build_section_taxonomy(main_product: str) -> list[str]:
    """Ordered list of value-chain sections. Order = display order in the CSV."""
    mp = main_product or "the main product"
    return [
        f"Manufacturers of {mp}",
        "OEMs / End-Product Manufacturers",
        "Other Component Manufacturers",
        "Technology Providers",
        "System Integrators",
        "EPC / Engineering",
        "Project Developers",
        "Suppliers / Raw Materials",
        f"Distributors of {mp}",
        "Other Component Distributors",
        "Research / Consulting",
        "Industry Bodies",
        "Other",
    ]


_ROLE_TO_SECTION: dict[str, str] = {
    "Supplier": "Suppliers / Raw Materials",
    "Technology Provider": "Technology Providers",
    "Integrator": "System Integrators",
    "EPC / Engineering": "EPC / Engineering",
    "Project Developer": "Project Developers",
    "Research / Consulting": "Research / Consulting",
    "Industry Body": "Industry Bodies",
    "Other": "Other",
}


def _canonicalize_section(label: str, taxonomy: list[str], main_product: str) -> str:
    """Map a free-ish LLM label onto exactly one taxonomy bucket. '' if no fit."""
    s = (label or "").strip()
    if not s:
        return ""
    low = s.lower()
    by_lower = {sec.lower(): sec for sec in taxonomy}
    if low in by_lower:
        return by_lower[low]

    mp = main_product or "the main product"
    is_other = ("other" in low) or ("component" in low) or ("sub-" in low) or ("subcomponent" in low)
    if "distribut" in low or "wholesal" in low or "dealer" in low or "reseller" in low:
        return "Other Component Distributors" if is_other else f"Distributors of {mp}"
    if "oem" in low or "end-product" in low or "end product" in low or "finished" in low:
        return "OEMs / End-Product Manufacturers"
    if "manufactur" in low or "producer" in low or "maker" in low or "fabricat" in low:
        return "Other Component Manufacturers" if is_other else f"Manufacturers of {mp}"
    if "technology" in low or "tech provider" in low or "software" in low or "platform" in low:
        return "Technology Providers"
    if "integrat" in low:
        return "System Integrators"
    if "epc" in low or "engineering" in low:
        return "EPC / Engineering"
    if "project develop" in low or "developer" in low:
        return "Project Developers"
    if "supplier" in low or "raw material" in low or "sourcing" in low:
        return "Suppliers / Raw Materials"
    if "research" in low or "consult" in low:
        return "Research / Consulting"
    if "industry body" in low or "associat" in low or "consortium" in low or "federation" in low:
        return "Industry Bodies"
    return ""


def section_for_row(
    verdict: dict[str, Any], taxonomy: list[str], main_product: str
) -> str:
    """Pick the section for one exported company row.

    The company's Role decides the top-level family (a Distributor can never land
    under Manufacturers). The LLM's value_chain_section only refines the sub-bucket
    *within* the manufacturer / distributor families (main product vs components vs
    end-product/OEM), since the LLM is unreliable at the family level itself.
    """
    mp = main_product or "the main product"
    role = str(verdict.get("role") or "").strip()
    llm = _canonicalize_section(
        str(verdict.get("value_chain_section") or ""), taxonomy, mp
    )

    mfr_family = {
        f"Manufacturers of {mp}",
        "Other Component Manufacturers",
        "OEMs / End-Product Manufacturers",
    }
    dist_family = {f"Distributors of {mp}", "Other Component Distributors"}

    if role == "Manufacturer":
        return llm if llm in mfr_family else f"Manufacturers of {mp}"
    if role == "Distributor":
        return llm if llm in dist_family else f"Distributors of {mp}"
    if role in _ROLE_TO_SECTION:
        return _ROLE_TO_SECTION[role]
    # Unknown / blank role — trust the LLM bucket if it gave one, else Other.
    return llm or "Other"


_STOPWORDS = {"of", "and", "the", "for", "to", "in", "a", "an", "&", "/", "-"}


def _stem(token: str) -> str:
    """Crude suffix-stripping so 'manufacturing'/'manufacturer'/'manufacturers' all match."""
    for suf in ("ing", "ers", "er", "ors", "or", "ion", "s"):
        if token.endswith(suf) and len(token) - len(suf) >= 4:
            return token[: -len(suf)]
    return token


def _words(text: str) -> set[str]:
    toks = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {_stem(t) for t in toks if t and t not in _STOPWORDS and len(t) > 1}


def match_custom_section(label: str, taxonomy: list[str]) -> str:
    """Map an LLM section label onto one of the CEO's exact categories. '' if none fit.

    Used when the run supplies an explicit section list (the CEO's wording). The LLM
    is told to pick verbatim from that list, but we tolerate small wording drift.
    """
    s = (label or "").strip()
    if not s:
        return ""
    by_lower = {sec.strip().lower(): sec for sec in taxonomy}
    low = s.lower().strip()
    if low in by_lower:
        return by_lower[low]
    label_words = _words(s)
    if not label_words:
        return ""
    best, best_score = "", 0.0
    for sec in taxonomy:
        if sec.lower() == "other":
            continue
        sw = _words(sec)
        if not sw:
            continue
        overlap = len(label_words & sw)
        score = overlap / min(len(label_words), len(sw))
        if score > best_score:
            best, best_score = sec, score
    return best if best_score >= 0.6 else ""


_SECTION_FAMILY_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("distributor", re.compile(r"distribut|wholesal|dealer|reseller", re.I)),
    ("manufacturer", re.compile(r"manufactur|oem|maker|producer|fabricat|cdmo|foundry", re.I)),
    ("supplier", re.compile(r"supplier|raw material", re.I)),
    ("integrator", re.compile(r"integrat", re.I)),
]

# Which company roles may appear under a section of each family.
_FAMILY_ROLES: dict[str, set[str]] = {
    "manufacturer": {"Manufacturer"},
    "distributor": {"Distributor", "Supplier"},
    "supplier": {"Supplier", "Distributor"},
    "integrator": {"Integrator"},
}


def _section_role_family(name: str) -> str | None:
    """The role family a section name implies, or None if ambiguous (vendor/provider/etc.)."""
    for family, pat in _SECTION_FAMILY_PATTERNS:
        if pat.search(name or ""):
            return family
    return None


def _role_compatible(family: str | None, role: str) -> bool:
    if not family:
        return True  # ambiguous section name → don't constrain by role
    allowed = _FAMILY_ROLES.get(family)
    return True if allowed is None else (role in allowed)


# Synonym phrases that should route to a section even if its exact name words are absent.
# Keyed by a concept word found in the section name → phrases to look for in company text.
_SECTION_SYNONYMS: dict[str, list[str]] = {
    "contract": [
        "contract manufactur", "contract manufacturer", "odm", "oem/odm", "private label",
        "private-label", "white label", "white-label", "toll manufactur", "build to print",
        "build-to-print", "contract assembly", "contract production",
    ],
    "cdmo": ["cdmo", "cmo", "contract development", "contract manufactur"],
    "distributor": ["distributor", "authorized distributor", "reseller", "dealer", "wholesale"],
    "integrator": ["system integrator", "integration services", "turnkey"],
    "supplier": ["supplier", "raw material", "components supplier", "ingredient"],
}


def _section_synonym_phrases(section_name: str) -> list[str]:
    low = section_name.lower()
    out: list[str] = []
    for concept, phrases in _SECTION_SYNONYMS.items():
        if concept in low:
            out.extend(phrases)
    return out


def _best_section_by_text(verdict: dict[str, Any], sections: list[str]) -> str | None:
    """Pick the section whose name best overlaps the company's text. None if no signal.

    Words that appear in only ONE section name (e.g. 'contract', 'cdmo', 'dermocosmetic')
    are distinctive and weighted higher; words shared across sections ('manufacturers',
    'cosmetics') barely discriminate and get low weight. Section-specific SYNONYM phrases
    (e.g. 'private label'/'ODM' -> a Contract Manufacturers section) also count.
    """
    # Driven by what the company actually does (products/functionality), NOT the LLM's
    # own section label — so a BMS maker routes to 'BMS Manufacturers' even if the LLM
    # lazily picked the generic 'Battery Pack Manufacturers'.
    text = " ".join(
        str(verdict.get(k) or "")
        for k in ("role_description", "key_products", "company", "company_summary")
    )
    tw = _words(text)
    if not tw:
        return None
    compact = re.sub(r"[^a-z0-9]", "", text.lower())  # for camelCase tokens (AlgoBMS -> bms)
    spaced = " " + re.sub(r"\s+", " ", text.lower()) + " "  # for synonym phrase matching
    # document frequency of each word across the candidate section names
    df: dict[str, int] = {}
    section_words = {s: _words(s) for s in sections}
    for sw in section_words.values():
        for w in sw:
            df[w] = df.get(w, 0) + 1
    best, best_score = None, 0.0
    for s in sections:
        score = 0.0
        for w in section_words[s]:
            distinctive = df.get(w, 0) == 1
            hit = w in tw or (distinctive and len(w) <= 4 and w in compact)
            if hit:
                score += 3.0 if distinctive else 0.5
        # Synonym phrases (e.g. 'private label' -> Contract Manufacturers) outweigh a bare
        # name-word match, so a contract/ODM company prefers Contract over generic OEMs.
        for phrase in _section_synonym_phrases(s):
            if phrase in spaced:
                score += 4.0
                break
        if score > best_score:
            best, best_score = s, score
    return best if best_score >= 1.0 else None


# Ordering of roles inside the single combined "Other" section.
_OTHER_ROLE_RANK: dict[str, int] = {
    "Distributor": 0,
    "Supplier": 1,
    "Technology Provider": 2,
    "Integrator": 3,
    "EPC / Engineering": 4,
    "Project Developer": 5,
    "Research / Consulting": 6,
    "Industry Body": 7,
}


def custom_section_for_row(verdict: dict[str, Any], taxonomy: list[str]) -> str:
    """Bucket a row into one of the requested categories, else the single 'Other' section.

    Rules (in order):
      1. Only sections whose family is compatible with the company's Role are eligible —
         a Distributor can never enter a 'Manufacturers' section.
      2. If no eligible requested section exists → 'Other'.
      3. Route by the company's actual products/functionality (distinctive keywords) so a
         BMS maker lands in 'BMS Manufacturers', an OEM in 'OEM ...', etc.
      4. Else use the LLM's explicit value_chain_section pick.
      5. Else default to the first eligible section.
    """
    # A seed/must-have with an analyst-assigned section is placed there outright.
    forced = match_custom_section(str(verdict.get("_forced_section") or ""), taxonomy)
    if forced:
        return forced
    role = str(verdict.get("role") or "").strip()
    eligible = [
        s
        for s in taxonomy
        if s.strip().lower() != "other"
        and _role_compatible(_section_role_family(s), role)
    ]
    if not eligible:
        return "Other"
    best = _best_section_by_text(verdict, eligible)
    if best:
        return best
    matched = match_custom_section(str(verdict.get("value_chain_section") or ""), eligible)
    if matched:
        return matched
    return eligible[0]


def group_into_sections(
    rows: list[dict[str, Any]],
    taxonomy: list[str],
    main_product: str,
    *,
    custom: bool = False,
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Return [(section_name, rows)] in taxonomy order, skipping empty sections.

    custom=True trusts the LLM's section pick against the supplied (CEO) list and
    never applies the auto role->section mapping; unplaceable rows go to 'Other'.
    """
    if custom:
        requested = [s for s in taxonomy if s.strip().lower() != "other"]
        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            buckets.setdefault(custom_section_for_row(row, requested), []).append(row)
        # Requested sections first (in taxonomy order)...
        result = [(s, buckets[s]) for s in requested if buckets.get(s)]
        # ...then ALL non-requested companies in ONE combined 'Other' section,
        # ordered by role (distributors, suppliers, tech, ...) for readability.
        other_rows: list[dict[str, Any]] = []
        for sec, rows_ in buckets.items():
            if sec not in requested:
                other_rows.extend(rows_)
        if other_rows:
            other_rows.sort(
                key=lambda r: (
                    _OTHER_ROLE_RANK.get(str(r.get("role") or ""), 99),
                    -float(r.get("quality_score") or r.get("confidence") or 0),
                )
            )
            result.append(("Other", other_rows))
        return result

    order = list(taxonomy)
    buckets = {sec: [] for sec in order}
    for row in rows:
        buckets.setdefault(section_for_row(row, order, main_product), []).append(row)
    ordered = [(sec, buckets[sec]) for sec in order if buckets.get(sec)]
    extras = [
        (sec, rows_) for sec, rows_ in buckets.items() if sec not in order and rows_
    ]
    return ordered + extras
