"""Phase 4 — LLM classification using smart_crawl data + rule signals."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from vendor_intel.clients.claude import ClaudeClient
from vendor_intel.config import Settings
from vendor_intel.intelligence.signal_extractor import extract_signals

_SYSTEM = """You classify companies for market landscape research.
Use ONLY the provided company info, crawl summary, signals, and query context.
Return JSON only with this exact shape:
{
  "company": "canonical name",
  "domain": "domain.com",
  "is_relevant": true or false,
  "role": "Manufacturer" or "Supplier" or "Distributor" or "Research / Consulting" or "Industry Body",
  "country_match": true or false,
  "confidence": 0.0 to 1.0
}
is_relevant=true for real operating companies in the target industry (e.g. chemical/plastic producers).
is_relevant=false for news sites, market research reports, directories, academia, stock tickers.
role MUST be one of: Manufacturer, Supplier, Distributor, Technology Provider, Integrator,
  EPC / Engineering, Project Developer, Industry Body, Other (use Other when role is unclear).
Set is_relevant=false for news, directories, market reports, government, universities, marketplaces."""

_SYSTEM_QUALITY = """You classify and describe companies for a CEO-level market landscape report.
Return JSON only — no markdown fences — with this exact schema:
{
  "company": "canonical company name",
  "brand": "trading/consumer brand name — ALWAYS set this; use company name when same or unknown",
  "parent": "Ownership — use ONE of: 'Independent' | 'Acquired by [Parent Co]' |
             'Subsidiary of [Parent Co]'. Only name a parent if stated on site or well-known;
             else 'Independent'. Never write 'Not stated'.",
  "domain": "domain.com",
  "is_relevant": true or false,
  "role": "Manufacturer" | "Supplier" | "Distributor" | "Technology Provider" | "Integrator"
        | "Research / Consulting" | "Industry Body" | "Other",
  "role_description": "Functionality (max 14 words): what this company DOES in the target market — concrete products/applications only.
                       Format: 'Manufacturer of [specific product/system] for [application]'.
                       GOOD: 'Manufacturer of brazed plate heat exchangers for heat pump chillers',
                       'Distributor of refrigeration components across Europe'.
                       BAD (never write): 'Leading manufacturer', 'European manufacturer', 'equipment manufacturer',
                       'brazed exchanger' alone, or any phrase that repeats manufacturer/distributor without naming products.",
  "key_products": "Max 4 items, comma-separated concrete products/services (not menu words).
                   Example: 'LED displays, signage CMS, video walls, media players'",
  "summary": "1-2 plain-English sentences describing the company: what it makes/does and who it
              serves in this market. Write it yourself in clean prose. NEVER copy website navigation,
              menus, cookie/contact text, slogans, markdown, or '[Home] [Products]' lists. If you only
              know the name and domain, write one careful sentence and stop.",
  "value_chain_section": "Pick EXACTLY ONE label from query_context.value_chain_sections that
                          best fits this company; if none fit, use 'Other'.",
  "country_match": true or false,
  "confidence": 0.0 to 1.0
}

VALUE_CHAIN_SECTION (group the company in the market's value chain):
- Choose ONE bucket verbatim from query_context.value_chain_sections.
- Pick the MOST SPECIFIC matching category — do NOT lazily put everything in the first/most
  general one. Read every category name and distinguish carefully:
    * a contract/private-label/white-label/CDMO maker → the contract/CDMO category (not the general one)
    * a dermatological/pharmacy/clinical variant → the dermocosmetic/specialised category if present
    * make sure 'value_chain_section' agrees with 'role' (a distributor must not get a 'Manufacturers' label)
- Only use 'Other' if the company genuinely fits NONE of the listed categories. Prefer a listed category.
- 'Manufacturers of <main product>' = makes the market's MAIN product itself.
- 'Other Component Manufacturers' = makes sub-components / parts that go into it.
- 'OEMs / End-Product Manufacturers' = makes the larger finished product the main product is used in.
- 'Distributors of <main product>' vs 'Other Component Distributors' follow the same main-vs-component split.
- Technology/software vendors → 'Technology Providers'; integrators → 'System Integrators'.

ROLE (assign from what the entity ACTUALLY does on its own site — do NOT default to Manufacturer):
- Manufacturer: makes/produces finished products itself (own factory/brand/formulations).
- Distributor: resells/distributes other brands' products; wholesaler; authorized dealer.
- Supplier: sells ingredients/raw materials/components to other companies.
- Technology Provider: software/SaaS/platform/equipment vendor.
- Research / Consulting: testing labs, consultancies, market-research firms.
- Industry Body: certification/standards bodies (e.g. NATRUE, ECOCERT, COSMOS), trade associations, federations.
- Only use Manufacturer when the company genuinely manufactures. A certifier or association is NEVER a Manufacturer.

RELEVANCE (validate against market_plan.include_keywords and market_definition):
- is_relevant=true when the company sells products/services IN the target market value chain
- is_relevant=false for: news, directories, market reports, clearly unrelated industries
- If market-related but crawl is thin, keep is_relevant=true and write the best description you can
- CMS / SaaS / software vendors → Technology Provider (not Manufacturer)
- role_description: becomes CSV Functionality — role-led phrase (Manufacturer/Distributor/OEM of X); not a product list
- key_products: comma-separated product names only
- brand: ALWAYS set (use company name when no separate brand)
- summary: your own clean 1-2 sentence description — never raw page text, menus, or marketing slogans"""

_VALID_ROLES = frozenset(
    {
        "Manufacturer",
        "Supplier",
        "Distributor",
        "Technology Provider",
        "Integrator",
        "EPC / Engineering",
        "Project Developer",
        "Research / Consulting",
        "Industry Body",
        "Other",
    }
)

_SYSTEM_RECALL = """You classify companies for a BROAD market landscape list (recall mode).
Include companies that might be tangentially related — noise is acceptable.
Return JSON only with this exact shape:
{
  "company": "canonical name",
  "domain": "domain.com",
  "is_relevant": true or false,
  "role": "Manufacturer" or "Distributor" or "Supplier" or "Other",
  "country_match": true or false,
  "confidence": 0.0 to 1.0
}
Set is_relevant=true for almost any real company website (chemical, plastic, sugar, ethanol, energy, ag, trade).
Only set is_relevant=false for obvious non-companies: news-only, pure directories, Wikipedia, stock quote pages."""

_HARD_BLOCK_DOMAIN = re.compile(
    r"(wikipedia\.org|facebook\.com|twitter\.com|x\.com|instagram\.com|"
    r"youtube\.com|tiktok\.com|pinterest\.com)",
    re.I,
)

_MEDIA_RESEARCH_DOMAIN = re.compile(
    r"(researchgate|marketresearch|nasdaq|sphericalinsights|intentmarket|datainsights|"
    r"expertmarket|chemdive|chemanalyst|packaginginsights|process-worldwide|wikipedia|"
    r"worldatlas|bloomberg|handwiki|geocountries|marketizer|industrysourcing|indexbox|"
    r"emergenresearch|coherentmarketinsights|freyrsolutions|travelbrazil|nationsonline|"
    r"bakingbusiness|materialpalette|theclimatedrive|masuuglobal|"
    r"linkedin|facebook|medium\.com|substack|\.edu$|\.gov\.br$|brazil\.gov)",
    re.I,
)

_ROLE_FROM_DOMAIN: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"distribu|wholesale|supplier|trader|trading", re.I), "Distributor"),
    (re.compile(r"export|import|trade\b", re.I), "Supplier"),
    (re.compile(r"university|unicamp|fapesp|cnpem|\.edu|lab\b|agencia\.", re.I), "Research / Consulting"),
    (re.compile(r"consortium|sugarcane\.org|biconsortium|unica|association", re.I), "Industry Body"),
    (re.compile(r"braskem|dow\.|solvay|raizen|oxiteno|petro|manufactur|plant|ethylene", re.I), "Manufacturer"),
]

_CHEM_COMPANY_HINT = re.compile(
    r"\b(?:braskem|dow|raizen|oxiteno|solvay|arkema|sabic|neste|basf|indorama|lyondell|"
    r"clariant|versalis|lanzatech|brenntag|mitsubishi|totalenergies|biotimize|biopoly|"
    r"petro|chem|plastic|ethylene|polymer|resin|industr|manufactur|"
    r"bio-?based|green\s+ethylene|renewable)\b",
    re.I,
)

_VALUE_CHAIN_MARKET = re.compile(
    r"\b(?:ethylene|bio[\s-]?based|renewable\s+chem|green\s+ethylene|polymer|petro|"
    r"plastic|bioethanol|feedstock|chemical)\b",
    re.I,
)


def _crawl_failed(smart_data: dict[str, Any]) -> bool:
    if smart_data.get("error"):
        return True
    data = smart_data.get("data")
    if not data:
        return True
    if isinstance(data, dict) and len(json.dumps(data)) < 80:
        return True
    return False


def _domain_is_media_or_research(domain: str) -> bool:
    low = (domain or "").lower()
    return bool(_MEDIA_RESEARCH_DOMAIN.search(low))


def _infer_role_from_name_domain(name: str, domain: str) -> str:
    blob = f"{name} {domain}"
    for pat, role in _ROLE_FROM_DOMAIN:
        if pat.search(blob):
            return role
    return "Manufacturer"


def _market_has_value_chain(query_context: dict[str, Any]) -> bool:
    """True for product/technology markets; False only for pure consulting/advisory queries."""
    blob = " ".join(
        [
            str(query_context.get("industry") or ""),
            str(query_context.get("market") or ""),
            " ".join(str(k) for k in (query_context.get("plan_keywords") or [])),
        ]
    ).lower()
    if re.search(r"\bconsult(ing|ancy)?\b|\badvisory\b|\banalyst\s+firm\b", blob):
        return False
    return True


def _market_is_chemical(query_context: dict[str, Any]) -> bool:
    """Backward-compatible alias — relevance boost applies to all value-chain markets."""
    return _market_has_value_chain(query_context)


def _role_from_company_function(fn: str) -> str | None:
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


_GENERIC_ROLE_DESC = re.compile(
    r"\b(?:participates in|as a (?:manufacturer|supplier|distributor|integrator|other)|"
    r"based on website content and classification signals)\b",
    re.I,
)


def _market_label(query_context: dict[str, Any]) -> str:
    scope = query_context.get("scope") if isinstance(query_context.get("scope"), dict) else {}
    return str(
        query_context.get("industry")
        or scope.get("market")
        or query_context.get("market")
        or "this market"
    ).strip()


def _market_terms(query_context: dict[str, Any]) -> list[str]:
    scope = query_context.get("scope") if isinstance(query_context.get("scope"), dict) else {}
    terms: list[str] = []
    for src in (
        scope.get("industry_terms"),
        scope.get("relevance_keywords"),
        query_context.get("plan_keywords"),
    ):
        if isinstance(src, list):
            terms.extend(str(t).strip() for t in src if len(str(t).strip()) >= 4)
    label = _market_label(query_context)
    terms.extend(w for w in re.findall(r"[a-z]{4,}", label.lower()) if w not in ("market", "brands"))
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out[:10]


def _is_boilerplate_description(text: str) -> bool:
    from vendor_intel.pipeline.csv_fields import is_weak_role_description

    return is_weak_role_description(text)


def _normalize_parent(raw: str, *, name: str, hq: str = "") -> str:
    """CEO-readable ownership — Independent or Acquired by / Subsidiary of."""
    p = re.sub(r"\s+", " ", (raw or "").strip())
    p = re.sub(r"[\u2013\u2014]", "-", p)
    p = re.sub(r"â€[\"\u201c\u201d]", "-", p)
    low = p.lower()
    if not p or low in ("not stated", "not stated on website", "unknown", "n/a"):
        return "Independent"
    if low.startswith("independent"):
        return "Independent" if not hq else f"Independent ({hq})"
    if re.search(r"\bacquired by\b", low):
        return p[:120]
    if re.search(r"\bsubsidiary of\b", low):
        return p[:120]
    if re.search(r"\bpart of\b|\bdivision of\b|\bowned by\b", low):
        m = re.search(
            r"(?:part of|division of|owned by)\s+(.+?)(?:\s*\(|$)",
            p,
            re.I,
        )
        if m:
            return f"Subsidiary of {m.group(1).strip()}"[:120]
    if "(" in p and ")" in p and name.lower() not in low:
        return p[:120]
    if len(p) > 3 and p.lower() != name.lower():
        return f"Subsidiary of {p}"[:120]
    return "Independent" if not hq else f"Independent ({hq})"


_DESCRIBE_SYSTEM = """You write CEO-level market landscape fields for one company.
Return JSON only — no markdown:
{
  "role_description": "Max 14 words — concrete products/systems + application (never 'leading manufacturer' or 'European manufacturer')",
  "key_products": "Max 4 comma-separated products/services (not website menu or social links).",
  "parent": "Independent | Acquired by [Company] | Subsidiary of [Company] — only if known"
}
Never list nav words (skip, navigation, facebook, brands, locations).
Never write 'participates in' or role labels like Manufacturer."""

_STRENGTHEN_SYSTEM = """You strengthen a weak company profile for a CEO market landscape report.
Use crawl excerpt + market_plan. If data is thin, infer cautiously from company name/domain.
Return JSON only:
{
  "is_relevant": true or false,
  "confidence": 0.0 to 1.0,
  "role": "Manufacturer" | "Supplier" | "Distributor" | "Technology Provider" | "Integrator" | "Other",
  "brand": "trading brand — ALWAYS set; use company name when same or unknown",
  "role_description": "Max 14 words — specific products/systems this company makes or sells in the market",
  "key_products": "Max 4 comma-separated products (not menu/nav words)",
  "parent": "Independent | Acquired by [Co] | Subsidiary of [Co]"
}
is_relevant=true when the company participates in the market_plan value chain.
Keep is_relevant=true if market keywords match but crawl was thin — write a strong description."""


def _usable_product_keywords(signals: dict[str, Any]) -> list[str]:
    from vendor_intel.pipeline.csv_fields import filter_product_keywords

    return filter_product_keywords([str(k) for k in (signals.get("keywords") or [])])


def _llm_fill_landscape_fields(
    client: ClaudeClient,
    *,
    name: str,
    domain: str,
    role: str,
    query_context: dict[str, Any],
    smart_data: dict[str, Any],
) -> dict[str, str]:
    """LLM one-liner when crawl/signals produced schema junk."""
    market = _market_label(query_context)
    excerpt = _smart_summary(smart_data, max_chars=2200) or f"Company: {name}, domain: {domain}"
    from vendor_intel.pipeline.market_relevance import market_context_summary

    _scope = query_context.get("scope") if isinstance(query_context.get("scope"), dict) else None
    user = json.dumps(
        {
            "company": {"name": name, "domain": domain},
            "market": market,
            "market_plan": market_context_summary(_scope, query_context),
            "role_hint": role,
            "query_context": {
                "industry": query_context.get("industry"),
                "country": query_context.get("country"),
            },
            "crawl_excerpt": excerpt,
        },
        indent=2,
    )[:5500]
    try:
        raw = client.complete_json(_DESCRIBE_SYSTEM, user, max_tokens=400)
        out = raw if isinstance(raw, dict) else {}
        from vendor_intel.pipeline.csv_fields import is_schema_junk_text, polish_role_description

        desc = polish_role_description(
            str(out.get("role_description") or ""),
            key_products=str(out.get("key_products") or ""),
            company=name,
            market=market,
            max_len=160,
        )
        from vendor_intel.pipeline.csv_fields import is_nav_keyword_junk, filter_product_keywords

        products = str(out.get("key_products") or "").strip()
        if is_schema_junk_text(products) or is_nav_keyword_junk(products):
            products = ""
        else:
            products = ", ".join(filter_product_keywords(products.split(",")))
        parent = _normalize_parent(
            str(out.get("parent") or ""),
            name=name,
            hq="",
        )
        if desc or products or parent != "Independent":
            from vendor_intel.pipeline.llm_meter import get_meter

            get_meter().add_classify(tokens_in=600, tokens_out=120)
            result: dict[str, str] = {}
            if desc:
                from vendor_intel.pipeline.csv_fields import (
                    finalize_role_description,
                    ROLE_DESCRIPTION_MAX_LEN,
                    ROLE_DESCRIPTION_MAX_WORDS,
                )

                result["role_description"] = finalize_role_description(
                    desc, max_len=ROLE_DESCRIPTION_MAX_LEN, max_words=ROLE_DESCRIPTION_MAX_WORDS
                )
            if products:
                from vendor_intel.pipeline.csv_fields import truncate_key_products

                result["key_products"] = truncate_key_products(products)
            if parent:
                result["parent"] = parent
            return result
    except Exception as exc:
        print(f"  [classify] describe LLM failed for {name[:35]}: {exc}", flush=True)
    return {}


def _needs_landscape_llm(result: dict[str, Any]) -> bool:
    from vendor_intel.pipeline.csv_fields import is_nav_keyword_junk, is_weak_role_description

    desc = str(result.get("role_description") or "")
    kp = str(result.get("key_products") or "")
    parent = str(result.get("parent") or "").lower()
    # Only a weak/missing description justifies an extra describe-LLM call on its own. Missing
    # products or an unstated parent alone used to trigger it for most rows (2x LLM calls/company);
    # now both must be missing. (The market-role pass later rewrites summary + functionality.)
    if is_weak_role_description(desc):
        return True
    if (not kp.strip() or is_nav_keyword_junk(kp)) and "not stated" in parent:
        return True
    return False


def _apply_landscape_llm_fill(
    result: dict[str, Any],
    *,
    client: ClaudeClient,
    name: str,
    domain: str,
    query_context: dict[str, Any],
    smart_data: dict[str, Any],
) -> dict[str, Any]:
    if not _needs_landscape_llm(result):
        return result
    filled = _llm_fill_landscape_fields(
        client,
        name=name,
        domain=domain,
        role=str(result.get("role") or "Supplier"),
        query_context=query_context,
        smart_data=smart_data,
    )
    if filled.get("role_description"):
        result["role_description"] = filled["role_description"]
    if filled.get("key_products"):
        result["key_products"] = filled["key_products"]
    if filled.get("parent"):
        result["parent"] = filled["parent"]
    if filled:
        result["landscape_strengthened"] = True
    return result


async def _aapply_landscape_llm_fill(
    result: dict[str, Any],
    *,
    client: ClaudeClient,
    name: str,
    domain: str,
    query_context: dict[str, Any],
    smart_data: dict[str, Any],
) -> dict[str, Any]:
    """Non-blocking wrapper: runs the (sync) describe-LLM fill in a worker thread. Skipped once
    the run's time budget is in wrap-up (the fill only polishes text fields)."""
    if not _needs_landscape_llm(result):
        return result
    from vendor_intel.pipeline.time_budget import get_budget

    if get_budget().wrap_up():
        return result
    return await asyncio.to_thread(
        lambda: _apply_landscape_llm_fill(
            result,
            client=client,
            name=name,
            domain=domain,
            query_context=query_context,
            smart_data=smart_data,
        )
    )


def _llm_strengthen_company(
    client: ClaudeClient,
    *,
    name: str,
    domain: str,
    role: str,
    query_context: dict[str, Any],
    smart_data: dict[str, Any],
    prior: dict[str, Any],
) -> dict[str, Any]:
    """Second-pass LLM for weak but market-related rows — relevance + landscape fields."""
    from vendor_intel.pipeline.market_relevance import market_context_summary

    _scope = query_context.get("scope") if isinstance(query_context.get("scope"), dict) else None
    excerpt = _smart_summary(smart_data, max_chars=3200) or f"Company: {name}, domain: {domain}"
    user = json.dumps(
        {
            "company": {"name": name, "domain": domain},
            "market_plan": market_context_summary(_scope, query_context),
            "prior_classification": {
                "role": prior.get("role"),
                "is_relevant": prior.get("is_relevant"),
                "role_description": prior.get("role_description"),
                "key_products": prior.get("key_products"),
                "confidence": prior.get("confidence"),
            },
            "crawl_excerpt": excerpt,
            "note": "Strengthen weak fields. Keep company if it fits the market; reject only if clearly off-market.",
        },
        indent=2,
    )[:6000]
    try:
        raw = client.complete_json(_STRENGTHEN_SYSTEM, user, max_tokens=500)
        out = raw if isinstance(raw, dict) else {}
        from vendor_intel.pipeline.csv_fields import (
            filter_product_keywords,
            is_nav_keyword_junk,
            is_weak_role_description,
            polish_role_description,
            truncate_key_products,
            ROLE_DESCRIPTION_MAX_LEN,
            ROLE_DESCRIPTION_MAX_WORDS,
        )

        desc = polish_role_description(
            str(out.get("role_description") or ""),
            key_products=str(out.get("key_products") or ""),
            company=name,
            market=_market_label(query_context),
            max_len=ROLE_DESCRIPTION_MAX_LEN,
        )
        products = str(out.get("key_products") or "").strip()
        if is_nav_keyword_junk(products):
            products = ""
        else:
            products = ", ".join(filter_product_keywords(products.split(",")))
        parent = _normalize_parent(str(out.get("parent") or ""), name=name, hq="")
        role_out = str(out.get("role") or role or "Other").strip()
        if role_out not in _VALID_ROLES:
            role_out = role or "Other"
        strengthened: dict[str, Any] = {
            "landscape_strengthened": True,
            "brand": _resolve_brand(str(out.get("brand") or ""), name),
            "parent": parent,
        }
        if desc and not is_weak_role_description(desc):
            from vendor_intel.pipeline.csv_fields import (
                finalize_role_description,
                ROLE_DESCRIPTION_MAX_LEN,
                ROLE_DESCRIPTION_MAX_WORDS,
            )

            strengthened["role_description"] = finalize_role_description(
                desc,
                max_len=ROLE_DESCRIPTION_MAX_LEN,
                max_words=ROLE_DESCRIPTION_MAX_WORDS,
            )
        if products:
            strengthened["key_products"] = truncate_key_products(products)
        if role_out:
            strengthened["role"] = role_out
        if "is_relevant" in out:
            strengthened["is_relevant"] = bool(out.get("is_relevant"))
        if out.get("confidence") is not None:
            strengthened["confidence"] = float(out.get("confidence") or 0)
        from vendor_intel.pipeline.llm_meter import get_meter

        get_meter().add_classify(tokens_in=800, tokens_out=180)
        print(f"  [classify] strengthened: {name[:35]}", flush=True)
        return strengthened
    except Exception as exc:
        print(f"  [classify] strengthen LLM failed for {name[:35]}: {exc}", flush=True)
        return {}


def _has_inscope_section(result: dict[str, Any]) -> bool:
    """The LLM (which read the site) placed this company in a REAL value-chain section —
    a stronger relevance signal than keyword counting. Used to stop a negative keyword
    score (often just adjacent-tech mentions like 'fiber'/'5G') from vetoing a real player."""
    sec = str(result.get("value_chain_section") or "").strip().lower()
    return bool(sec) and sec not in ("other", "unknown", "n/a", "none", "uncategorized")


def _llm_confident_inmarket(result: dict[str, Any], kw_prof: Any) -> bool:
    """Strong LLM in-market signal that should override a negative/junky KEYWORD score.

    True when the LLM (which read the page) placed the company in a REAL in-scope section AS a
    maker/provider AND its clean summary text explicitly names an in-market product. For big
    diversified vendors (e.g. a giant IVD maker whose target line is a small part of a nav/cookie-
    heavy site) the negative keyword score is a crawl-quality artifact, not true off-market — this
    rescues them without opening the door to genuine noise (the bar is section + role + product)."""
    if not _has_inscope_section(result):
        return False
    role = str(result.get("role") or "").strip().lower()
    if not any(
        t in role
        for t in ("manufactur", "maker", "developer", "producer", "provider", "supplier")
    ):
        return False
    from vendor_intel.pipeline.market_relevance import include_keyword_hits

    summary = " ".join(
        str(result.get(k) or "")
        for k in ("company_summary", "summary", "role_description", "key_products")
    )
    return include_keyword_hits(summary, kw_prof) >= 1


def _is_clearly_off_market(
    *,
    score: int,
    junk: int,
    is_relevant: bool,
    has_section: bool = False,
    llm_strong: bool = False,
) -> bool:
    """Only hard-reject obvious junk / zero market fit — do not cut weak-but-related rows.

    A strong LLM in-market signal (real in-scope section + maker/provider role + an explicit
    in-market product in the clean summary) overrides a negative/junky KEYWORD score, which for
    diversified vendors is usually a crawl-quality artifact (nav/cookie chrome), not off-market."""
    if llm_strong:
        return False
    if junk >= 4 and score < 1:
        return True
    # A negative keyword score vetoes only when the LLM did NOT slot it into a real section.
    # (Operators/service providers often score negative on adjacent-tech words yet are real.)
    if score < 0 and not is_relevant and not has_section:
        return True
    return False


def _potentially_market_related(
    *,
    score: int,
    result: dict[str, Any],
    signals: dict[str, Any],
    is_seed: bool,
    registry: bool,
) -> bool:
    if is_seed or registry:
        return True
    if score >= 1:
        return True
    if result.get("is_relevant"):
        return True
    if signals.get("industry_match"):
        return True
    # LLM placed it in a real in-scope section → trust that over a negative keyword score.
    if _has_inscope_section(result):
        return True
    return False


def _needs_strengthen(
    result: dict[str, Any],
    *,
    score: int,
    smart_data: dict[str, Any] | None,
) -> bool:
    from vendor_intel.pipeline.csv_fields import is_nav_keyword_junk, is_weak_role_description

    """Second-pass (supplement fetch + strengthen LLM) ONLY for genuinely ambiguous rows.

    Previously this fired on almost every row (any of: weak text, keyword score < 2, thin crawl,
    confidence < 0.62), doubling the LLM calls and triggering slow supplement crawls. Now a row
    must be BOTH low/mid-confidence (the classifier itself was unsure) AND lack evidence (thin
    crawl, no/weak description, or no keyword support). Confident rows are never re-checked, and
    rows whose crawl content is rich enough with a usable description are left alone."""
    desc = str(result.get("role_description") or "")
    kp = str(result.get("key_products") or "")
    conf = float(result.get("confidence") or 0)
    if conf >= 0.6:
        return False
    weak_desc = (not desc.strip()) or is_weak_role_description(desc) or is_nav_keyword_junk(kp)
    thin = _crawl_failed(smart_data or {}) or len(_smart_summary(smart_data)) < 200
    no_kw_support = score < 1
    return thin or (weak_desc and no_kw_support)


def _build_what_they_do(
    name: str,
    query_context: dict[str, Any],
    signals: dict[str, Any],
    smart_data: dict[str, Any] | None = None,
) -> str:
    """Query-specific activity blurb — what the company does in this market."""
    from vendor_intel.pipeline.csv_fields import (
        extract_company_summary,
        polish_role_description,
        ROLE_DESCRIPTION_MAX_LEN,
    )

    market = _market_label(query_context)
    kws = _usable_product_keywords(signals)
    key_products = ", ".join(kws) if kws else ""

    summary = extract_company_summary(smart_data) if smart_data else ""
    polished = polish_role_description(
        summary,
        key_products=key_products,
        company=name,
        market=market,
        max_len=ROLE_DESCRIPTION_MAX_LEN,
    )
    return polished


def _default_landscape_fields(
    name: str,
    role: str,
    query_context: dict[str, Any],
    signals: dict[str, Any],
    smart_data: dict[str, Any] | None = None,
) -> dict[str, str]:
    hq = str(signals.get("hq_country") or "").strip()
    parent = f"Independent ({hq})" if hq else "Independent"
    kws = _usable_product_keywords(signals)
    key_products = ", ".join(kws) if kws else ""
    role_description = _build_what_they_do(name, query_context, signals, smart_data)
    return {
        "brand": name.strip(),
        "parent": parent,
        "role_description": role_description,
        "key_products": key_products,
    }


def _resolve_brand(brand: str, name: str) -> str:
    """CEO CSV always shows a brand — default to company name when LLM leaves it empty."""
    b = (brand or "").strip()
    n = (name or "").strip()
    return b or n


def _classification_blob(
    name: str,
    domain: str,
    result: dict[str, Any],
    signals: dict[str, Any],
    smart_data: dict[str, Any] | None = None,
) -> str:
    return (
        f"{name} {domain} "
        f"{result.get('role_description') or ''} "
        f"{result.get('key_products') or ''} "
        f"{json.dumps(signals)} "
        f"{_smart_summary(smart_data)}"
    )


def _fix_role_from_signals(role: str, signals: dict[str, Any]) -> str:
    r = (role or "").strip()
    if r and r != "Other":
        return r
    if signals.get("is_technology_provider"):
        return "Technology Provider"
    if signals.get("is_manufacturer"):
        return "Manufacturer"
    if signals.get("is_distributor"):
        return "Distributor"
    if signals.get("is_supplier"):
        return "Supplier"
    if signals.get("is_services"):
        return "Integrator"
    return r or "Other"


def _merge_llm_landscape_fields(
    result: dict[str, Any],
    out: dict[str, Any],
    *,
    name: str,
    signals: dict[str, Any],
    query_context: dict[str, Any],
    smart_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach CEO-report fields from LLM output; fill gaps from signals."""
    defaults = _default_landscape_fields(
        name,
        str(result.get("role") or "Supplier"),
        query_context,
        signals,
        smart_data=smart_data,
    )
    result["brand"] = _resolve_brand(str(out.get("brand") or ""), name)
    hq = str(signals.get("hq_country") or "").strip()
    result["parent"] = _normalize_parent(
        str(out.get("parent") or "").strip() or defaults["parent"],
        name=name,
        hq=hq,
    )
    from vendor_intel.pipeline.csv_fields import (
        filter_product_keywords,
        finalize_role_description,
        is_nav_keyword_junk,
        polish_role_description,
        ROLE_DESCRIPTION_MAX_LEN,
        ROLE_DESCRIPTION_MAX_WORDS,
        truncate_key_products,
    )

    raw_kp = str(out.get("key_products") or "").strip() or defaults["key_products"]
    if is_nav_keyword_junk(raw_kp):
        raw_kp = defaults["key_products"] if not is_nav_keyword_junk(defaults["key_products"]) else ""
    key_products = ", ".join(
        filter_product_keywords([x.strip() for x in raw_kp.split(",") if x.strip()])
    )
    raw_desc = str(out.get("role_description") or "").strip()
    if _is_boilerplate_description(raw_desc):
        raw_desc = ""
    polished = polish_role_description(
        raw_desc,
        key_products=key_products,
        company=name,
        market=_market_label(query_context),
        max_len=ROLE_DESCRIPTION_MAX_LEN,
    )

    result["role_description"] = finalize_role_description(
        polished or raw_desc,
        max_len=ROLE_DESCRIPTION_MAX_LEN,
        max_words=ROLE_DESCRIPTION_MAX_WORDS,
    )
    result["key_products"] = truncate_key_products(key_products)
    result["role"] = _fix_role_from_signals(str(result.get("role") or ""), signals)
    return result


async def _strengthen_weak_row(
    result: dict[str, Any],
    *,
    client: ClaudeClient | None,
    name: str,
    domain: str,
    signals: dict[str, Any],
    query_context: dict[str, Any],
    smart_data: dict[str, Any],
    is_seed: bool,
    industry_kws: list[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Recover weak rows via supplement crawl + LLM — only drop clearly off-market junk."""
    from vendor_intel.discovery.company_registry import is_registry_company
    from vendor_intel.pipeline.market_relevance import (
        keyword_profile,
        market_relevance_score,
        universal_junk_penalty,
    )

    result["brand"] = _resolve_brand(str(result.get("brand") or ""), name)
    scope = query_context.get("scope") if isinstance(query_context.get("scope"), dict) else None
    registry = is_seed or is_registry_company(name, scope)
    kw_prof = keyword_profile(scope, query_context)
    blob = _classification_blob(name, domain, result, signals, smart_data)
    score = market_relevance_score(blob, kw_prof, domain=domain, name=name)
    junk = universal_junk_penalty(blob)
    conf = float(result.get("confidence") or 0)
    result["market_relevance_score"] = score

    if is_seed:
        return result, smart_data, signals

    llm_strong = _llm_confident_inmarket(result, kw_prof)
    if _is_clearly_off_market(
        score=score, junk=junk, is_relevant=bool(result.get("is_relevant")),
        has_section=_has_inscope_section(result), llm_strong=llm_strong,
    ):
        result["is_relevant"] = False
        result["confidence"] = min(conf, 0.35)
        return result, smart_data, signals

    # Strong LLM in-market signal: trust it over a junky/negative keyword score so diversified
    # vendors (real in-scope maker with an explicit in-market product in the summary) aren't cut.
    # Mark relevant, lift confidence above the export floor, and flag it so the export gate's
    # keyword-based vetoes (weak_product_fit / weak_other_role) also defer to it.
    if llm_strong:
        result["llm_inmarket_trusted"] = True
        if not result.get("is_relevant"):
            result["is_relevant"] = True
            result["confidence"] = max(conf, 0.60)

    related = _potentially_market_related(
        score=score,
        result=result,
        signals=signals,
        is_seed=is_seed,
        registry=registry,
    )
    if not related:
        result["is_relevant"] = False
        return result, smart_data, signals

    if related and not result.get("is_relevant"):
        result["is_relevant"] = True
        result["confidence"] = max(conf, 0.52 if score >= 1 else 0.48)

    from vendor_intel.pipeline.time_budget import get_budget

    if (
        _needs_strengthen(result, score=score, smart_data=smart_data)
        and client
        and client.available
        and not get_budget().wrap_up()
    ):
        if _crawl_failed(smart_data) or len(_smart_summary(smart_data)) < 400:
            from vendor_intel.enrichment.smart_enrichment import supplement_crawl

            smart_data = await supplement_crawl(domain, smart_data)
            signals = extract_signals(smart_data, industry_keywords=industry_kws)
            blob = _classification_blob(name, domain, result, signals, smart_data)
            score = market_relevance_score(blob, kw_prof, domain=domain, name=name)
            result["market_relevance_score"] = score

        boosted = await asyncio.to_thread(
            _llm_strengthen_company,
            client,
            name=name,
            domain=domain,
            role=str(result.get("role") or "Other"),
            query_context=query_context,
            smart_data=smart_data,
            prior=result,
        )
        if boosted:
            result.update({k: v for k, v in boosted.items() if v is not None and v != ""})
            conf = float(result.get("confidence") or conf)
            if result.get("is_relevant"):
                result["confidence"] = max(conf, 0.55 if score >= 2 else 0.52)

        if _needs_strengthen(result, score=score, smart_data=smart_data):
            result = await _aapply_landscape_llm_fill(
                result,
                client=client,
                name=name,
                domain=domain,
                query_context=query_context,
                smart_data=smart_data,
            )

    result["brand"] = _resolve_brand(str(result.get("brand") or ""), name)
    return result, smart_data, signals


async def _finalize_quality_classify(
    result: dict[str, Any],
    *,
    client: ClaudeClient | None,
    name: str,
    domain: str,
    signals: dict[str, Any],
    query_context: dict[str, Any],
    smart_data: dict[str, Any],
    is_seed: bool,
    industry_kws: list[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    result = _apply_quality_relevance_boost(
        result,
        name=name,
        domain=domain,
        signals=signals,
        query_context=query_context,
        is_seed=is_seed,
        smart_data=smart_data,
    )
    return await _strengthen_weak_row(
        result,
        client=client,
        name=name,
        domain=domain,
        signals=signals,
        query_context=query_context,
        smart_data=smart_data,
        is_seed=is_seed,
        industry_kws=industry_kws,
    )


def _apply_quality_relevance_boost(
    result: dict[str, Any],
    *,
    name: str,
    domain: str,
    signals: dict[str, Any],
    query_context: dict[str, Any],
    is_seed: bool,
    smart_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep market-related rows — weak ones are strengthened later, not cut here."""
    from vendor_intel.discovery.company_registry import is_registry_company
    from vendor_intel.pipeline.market_relevance import keyword_profile, market_relevance_score
    from vendor_intel.pipeline.participant_domains import is_market_research_entity

    landscape = {
        k: result[k]
        for k in ("brand", "parent", "role_description", "key_products")
        if result.get(k)
    }

    if is_market_research_entity(name, domain):
        result["is_relevant"] = False
        result["confidence"] = min(float(result.get("confidence") or 0), 0.2)
        result["role"] = "Research / Consulting"
        result.update(landscape)
        result["brand"] = _resolve_brand(str(result.get("brand") or ""), name)
        return result

    scope = query_context.get("scope") if isinstance(query_context.get("scope"), dict) else None
    registry = is_seed or is_registry_company(name, scope)
    kw_prof = keyword_profile(scope, query_context)
    blob = _classification_blob(name, domain, result, signals, smart_data)
    mscore = market_relevance_score(blob, kw_prof, domain=domain, name=name)
    participant = bool(
        registry
        or mscore >= 1
        or signals.get("industry_match")
        or result.get("is_relevant")
    )

    if registry:
        result["is_relevant"] = True
        result["confidence"] = max(float(result.get("confidence") or 0), 0.62)
        if not landscape:
            result.update(_default_landscape_fields(
                name, str(result.get("role") or "Supplier"), query_context, signals,
                smart_data=smart_data,
            ))
        else:
            result.update(landscape)
        result["brand"] = _resolve_brand(str(result.get("brand") or ""), name)
        return result

    conf = float(result.get("confidence") or 0)
    if participant and mscore >= 1 and not result.get("is_relevant") and conf >= 0.4:
        result["is_relevant"] = True
        result["confidence"] = max(conf, 0.5)
    elif participant and result.get("is_relevant"):
        result["confidence"] = max(conf, 0.55 if mscore >= 2 else 0.5)

    if not landscape:
        result.update(_default_landscape_fields(
            name, str(result.get("role") or "Supplier"), query_context, signals,
            smart_data=smart_data,
        ))
    else:
        result.update(landscape)
    result["brand"] = _resolve_brand(str(result.get("brand") or ""), name)
    return result


def _looks_like_industrial_company(name: str, domain: str) -> bool:
    """Heuristic: real operating company in industrial/tech/energy markets (not media/directory)."""
    if _domain_is_media_or_research(domain):
        return False
    blob = f"{name} {domain}".lower()
    if _CHEM_COMPANY_HINT.search(blob):
        return True
    if any(x in domain for x in (".com.br", "petro", "chem", "plastic", "poly", "bio")):
        return True
    tech_hints = (
        "corp", "gmbh", "inc", "ltd", "llc", "co.", "sa.", "bv.", "nv.", "oy", "ab", "plc",
        "group", "holding", "tech", "system", "solution", "instrument", "precision", "manufactur",
        "engineer", "industrial", "wind", "turbine", "energy", "power", "grid", "renewable",
        "cnc", "machining", "simulation", "software", "gcode", "g-code", "cam",
        "clock", "frequenc", "timing", "oscillat", "quartz", "atomic", "resonat", "calibrat",
        "sensor", "gnss", "gps", "control", "motion", "drive", "servo", "automat",
    )
    return any(hint in blob for hint in tech_hints)


def _rule_fallback_recall(
    company: dict[str, str],
    smart_data: dict[str, Any],
    query_context: dict[str, Any],
) -> dict[str, Any]:
    """Permissive fallback — keep almost all discovered companies."""
    name = company.get("name") or ""
    domain = company.get("domain") or ""
    if _HARD_BLOCK_DOMAIN.search(domain or ""):
        return {
            "company": name,
            "domain": domain,
            "is_relevant": False,
            "role": "Other",
            "country_match": False,
            "confidence": 0.9,
        }

    industry_kws = list(query_context.get("functions") or []) + [query_context.get("industry") or ""]
    sig = extract_signals(smart_data, industry_keywords=industry_kws)
    crawl_ok = not _crawl_failed(smart_data)
    blob = _flatten_blob(smart_data, name, domain)

    relevant = True
    if sig.get("is_manufacturer"):
        role = "Manufacturer"
    elif sig.get("is_distributor"):
        role = "Distributor"
    elif sig.get("is_supplier"):
        role = "Supplier"
    else:
        role = _infer_role_from_name_domain(name, domain)

    country = (query_context.get("country") or "").strip().lower()
    country_match = (
        not country
        or country == "global"
        or country in blob
        or (country == "brazil" and ".br" in domain)
    )
    conf = 0.75 if crawl_ok and sig.get("industry_match") else (0.6 if crawl_ok else 0.5)

    return {
        "company": name,
        "domain": domain,
        "is_relevant": relevant,
        "role": role,
        "country_match": country_match,
        "confidence": conf,
    }


def _flatten_blob(smart_data: dict[str, Any], name: str, domain: str) -> str:
    return (json.dumps(smart_data).lower() + f" {name} {domain}").lower()


def _rule_fallback(
    company: dict[str, str],
    smart_data: dict[str, Any],
    query_context: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic fallback when LLM unavailable or crawl failed."""
    name = company.get("name") or ""
    domain = company.get("domain") or ""
    industry_kws = list(query_context.get("functions") or []) + [query_context.get("industry") or ""]
    sig = extract_signals(smart_data, industry_keywords=industry_kws)

    if _domain_is_media_or_research(domain):
        blocked = {
            "company": name,
            "domain": domain,
            "is_relevant": False,
            "role": "Research / Consulting",
            "country_match": False,
            "confidence": 0.85,
        }
        blocked.update(_default_landscape_fields(name, "Research / Consulting", query_context, sig))
        return blocked

    crawl_ok = not _crawl_failed(smart_data)
    blob = json.dumps(smart_data).lower() + " " + domain.lower()
    relevant = crawl_ok and len(json.dumps(smart_data.get("data") or {})) > 80
    if sig.get("industry_match"):
        relevant = True

    # Quality mode: do not mark relevant on name alone without content (reduces junk)
    if not crawl_ok and _looks_like_industrial_company(name, domain):
        relevant = bool(sig.get("industry_match"))

    if sig.get("is_manufacturer"):
        role = "Manufacturer"
    elif sig.get("is_distributor"):
        role = "Distributor"
    elif sig.get("is_supplier"):
        role = "Supplier"
    elif _looks_like_industrial_company(name, domain):
        role = _infer_role_from_name_domain(name, domain)
    elif re.search(r"consortium|association|council|federation", blob, re.I):
        role = "Industry Body"
    elif re.search(r"consult|advisory|research|insights", blob, re.I):
        role = "Research / Consulting"
    else:
        role = "Supplier"

    country = (query_context.get("country") or "").strip().lower()
    country_match = (
        not country
        or country == "global"
        or country in blob
        or (country == "brazil" and ".br" in domain)
    )

    conf = 0.72 if relevant and crawl_ok else (0.55 if relevant else 0.25)

    base = {
        "company": name,
        "domain": domain,
        "is_relevant": relevant,
        "role": role,
        "country_match": country_match,
        "confidence": conf,
    }
    base.update(_default_landscape_fields(name, role, query_context, sig))
    return base


def _smart_summary(smart_data: dict[str, Any], max_chars: int = 2500) -> str:
    if not smart_data:
        return ""
    if smart_data.get("error"):
        return f"crawl_error: {smart_data.get('error')}"
    data = smart_data.get("data") or {}
    try:
        text = json.dumps(data, ensure_ascii=False)[:max_chars]
    except Exception:
        text = str(data)[:max_chars]
    return text


def _pack_classify_result(
    result: dict[str, Any],
    smart_data: dict[str, Any],
    signals: dict[str, Any],
) -> dict[str, Any]:
    packed = dict(result)
    packed["_enriched"] = smart_data
    packed["_classify_signals"] = signals
    return packed


async def classify_company(
    company: dict[str, str],
    smart_data: dict[str, Any],
    query_context: dict[str, Any],
    *,
    settings: Settings | None = None,
    client: ClaudeClient | None = None,
    recall_mode: bool = False,
    quality_mode: bool = False,
    is_seed: bool = False,
) -> dict[str, Any]:
    """
    Classify one company.

    company: {name, domain}
    query_context: {industry, country, functions: list[str]}
    """
    settings = settings or Settings.load()
    recall_mode = recall_mode or bool(getattr(settings, "pipeline_recall_mode", False))
    profile = str(getattr(settings, "pipeline_profile", "quality") or "quality")
    quality_mode = quality_mode or (
        not recall_mode and profile in ("quality", "balanced")
    )
    name = str(company.get("name") or "").strip()
    domain = str(company.get("domain") or "").strip()
    company_function = str(company.get("company_function") or "").strip()

    from vendor_intel.pipeline.entity_gate import reject_reason as entity_reject
    from vendor_intel.pipeline.participant_domains import is_market_research_entity

    scope = query_context.get("scope") if isinstance(query_context.get("scope"), dict) else None
    industry_kws = list(query_context.get("plan_keywords") or []) or list(
        query_context.get("functions") or []
    ) + [query_context.get("industry") or ""]
    if scope:
        for field in ("include_keywords", "relevance_keywords", "industry_terms"):
            vals = scope.get(field)
            if isinstance(vals, list):
                industry_kws.extend(str(v) for v in vals)
    signals = extract_signals(smart_data, industry_keywords=industry_kws)

    if is_market_research_entity(name, domain):
        empty_sig: dict[str, Any] = {}
        blocked = {
            "company": name,
            "domain": domain,
            "is_relevant": False,
            "role": "Research / Consulting",
            "country_match": False,
            "confidence": 0.15,
            "reject_reason": "market_research_site",
        }
        blocked.update(_default_landscape_fields(name, "Research / Consulting", query_context, empty_sig))
        return blocked

    # Seeds/registry majors: skip text-heavy entity gate (corporate sites often match "read more", etc.)
    from vendor_intel.discovery.company_registry import is_registry_company

    client = client or ClaudeClient(settings)
    skip_entity_gate = (
        is_seed
        or is_registry_company(name, scope)
        or (quality_mode and client.available)
    )

    gate = None if skip_entity_gate else entity_reject(name, domain, text=_smart_summary(smart_data))
    if gate:
        blocked = {
            "company": name,
            "domain": domain,
            "is_relevant": False,
            "role": "Research / Consulting",
            "country_match": False,
            "confidence": 0.9,
            "reject_reason": gate,
        }
        blocked.update(_default_landscape_fields(name, "Research / Consulting", query_context, signals))
        return blocked

    if recall_mode and _HARD_BLOCK_DOMAIN.search(domain):
        return _rule_fallback_recall(company, smart_data, query_context)

    if not recall_mode and _domain_is_media_or_research(domain):
        return _rule_fallback(company, smart_data, query_context)

    if recall_mode and not client.available:
        return _rule_fallback_recall(company, smart_data, query_context)

    if recall_mode:
        system = _SYSTEM_RECALL
    elif quality_mode:
        system = _SYSTEM_QUALITY
    else:
        system = _SYSTEM

    # Quality: thin crawl — still use LLM (name + domain + partial signals) when available
    if quality_mode and _crawl_failed(smart_data) and not client.available and not is_seed:
        if not signals.get("industry_match") and not _looks_like_industrial_company(name, domain):
            print(f"  [classify] {name[:35]} — thin crawl, no LLM, skip (quality)", flush=True)
            role = _infer_role_from_name_domain(name, domain)
            skipped = {
                "company": name,
                "domain": domain,
                "is_relevant": False,
                "role": role,
                "country_match": False,
                "confidence": 0.25,
            }
            skipped.update(_default_landscape_fields(name, role, query_context, signals))
            return skipped
        return _rule_fallback(company, smart_data, query_context)

    if _crawl_failed(smart_data) and client.available and not recall_mode:
        print(f"  [classify] {name[:35]} — crawl thin, LLM classify (name/domain)", flush=True)
        thin_system = system if quality_mode else _SYSTEM
        from vendor_intel.pipeline.market_relevance import market_context_summary

        _scope = query_context.get("scope") if isinstance(query_context.get("scope"), dict) else None
        user = json.dumps(
            {
                "company": {"name": name, "domain": domain},
                "query_context": query_context,
                "market_plan": market_context_summary(_scope, query_context),
                "signals": signals,
                "crawl_summary": _smart_summary(smart_data) or "no_website_content",
                "note": "Classify from company name and domain when crawl data is missing.",
            },
            indent=2,
        )[:6000]
        try:
            max_tok = 900 if quality_mode else 512
            raw = await asyncio.to_thread(
                lambda: client.complete_json(thin_system, user, max_tokens=max_tok)
            )
            out = raw if isinstance(raw, dict) else {}
            co_out = out.get("company")
            if isinstance(co_out, dict):
                co_out = co_out.get("name") or name
            result = {
                "company": str(co_out or name).strip(),
                "domain": str(out.get("domain") or domain),
                "is_relevant": bool(out.get("is_relevant")),
                "role": str(out.get("role") or "Other"),
                "value_chain_section": str(out.get("value_chain_section") or "").strip(),
                "summary": str(out.get("summary") or "").strip(),
                "country_match": bool(out.get("country_match")),
                "confidence": float(out.get("confidence") or 0.45),
            }
            if result["role"] not in _VALID_ROLES:
                result["role"] = _infer_role_from_name_domain(name, domain)
            result["confidence"] = min(result["confidence"], 0.72)
            if quality_mode:
                result = _merge_llm_landscape_fields(
                    result,
                    out,
                    name=name,
                    signals=signals,
                    query_context=query_context,
                    smart_data=smart_data,
                )
                result = await _aapply_landscape_llm_fill(
                    result,
                    client=client,
                    name=name,
                    domain=domain,
                    query_context=query_context,
                    smart_data=smart_data,
                )
            from vendor_intel.pipeline.llm_meter import get_meter

            get_meter().add_classify()
            if quality_mode:
                result, smart_data, signals = await _finalize_quality_classify(
                    result,
                    client=client,
                    name=name,
                    domain=domain,
                    signals=signals,
                    query_context=query_context,
                    smart_data=smart_data,
                    is_seed=is_seed,
                    industry_kws=industry_kws,
                )
            print(
                f"  [classify] {name[:35]} → relevant={result['is_relevant']} "
                f"role={result['role']} conf={result['confidence']:.2f} (thin)",
                flush=True,
            )
            return _pack_classify_result(result, smart_data, signals)
        except Exception as exc:
            print(f"  [classify] thin LLM failed for {name[:40]}: {exc}", flush=True)

    if _crawl_failed(smart_data) and recall_mode:
        fb = _rule_fallback_recall(company, smart_data, query_context)
        print(
            f"  [classify] {name[:35]} → relevant={fb['is_relevant']} "
            f"role={fb['role']} conf={fb['confidence']:.2f} (recall)",
            flush=True,
        )
        return fb

    if _crawl_failed(smart_data):
        print(f"  [classify] {name[:35]} — crawl thin, using rules", flush=True)
        fb = _rule_fallback(company, smart_data, query_context)
        if quality_mode:
            fb, smart_data, signals = await _finalize_quality_classify(
                fb,
                client=client,
                name=name,
                domain=domain,
                signals=signals,
                query_context=query_context,
                smart_data=smart_data,
                is_seed=is_seed,
                industry_kws=industry_kws,
            )
            return _pack_classify_result(fb, smart_data, signals)
        return fb

    if not client.available:
        print(f"  [classify] LLM off — rule fallback for {name[:40]}", flush=True)
        return (
            _rule_fallback_recall(company, smart_data, query_context)
            if recall_mode
            else _rule_fallback(company, smart_data, query_context)
        )

    from vendor_intel.pipeline.market_relevance import market_context_summary

    _scope = query_context.get("scope") if isinstance(query_context.get("scope"), dict) else None
    user = json.dumps(
        {
            "company": {"name": name, "domain": domain},
            "query_context": query_context,
            "market_plan": market_context_summary(_scope, query_context),
            "signals": {
                **signals,
                "hq_country": signals.get("hq_country"),
                "mentioned_countries": signals.get("mentioned_countries"),
            },
            "crawl_summary": _smart_summary(smart_data),
            "recall_mode": recall_mode,
            "quality_mode": quality_mode,
            "is_seed": is_seed,
            "note": (
                "Phase 1 seed — treat as market participant "
                "unless the site is clearly news/directory/research-only."
                if is_seed
                else f"Target market: {_market_label(query_context)}. "
                "Classify relevance vs market_plan. If market-related but data is thin, "
                "still set is_relevant=true and write the best role_description you can."
            ),
        },
        indent=2,
    )[:6000]

    try:
        max_tok = 900 if quality_mode else 512
        raw = await asyncio.to_thread(
            lambda: client.complete_json(system, user, max_tokens=max_tok)
        )
        out = raw if isinstance(raw, dict) else {}
        co_out = out.get("company")
        if isinstance(co_out, dict):
            co_out = co_out.get("name") or name
        result = {
            "company": str(co_out or name).strip(),
            "domain": str(out.get("domain") or domain),
            "is_relevant": bool(out.get("is_relevant")),
            "role": str(out.get("role") or "Other"),
            "value_chain_section": str(out.get("value_chain_section") or "").strip(),
            "summary": str(out.get("summary") or "").strip(),
            "country_match": bool(out.get("country_match")),
            "confidence": float(out.get("confidence") or 0.5),
        }
        if quality_mode:
            result = _merge_llm_landscape_fields(
                result,
                out,
                name=name,
                signals=signals,
                query_context=query_context,
                smart_data=smart_data,
            )
            result = await _aapply_landscape_llm_fill(
                result,
                client=client,
                name=name,
                domain=domain,
                query_context=query_context,
                smart_data=smart_data,
            )
        if recall_mode and not result["is_relevant"] and _looks_like_industrial_company(name, domain):
            result["is_relevant"] = True
            result["confidence"] = max(result["confidence"], 0.45)
        if recall_mode and result["is_relevant"] is False and not _HARD_BLOCK_DOMAIN.search(domain):
            result["is_relevant"] = True
            result["confidence"] = max(result["confidence"], 0.4)
        if quality_mode:
            result, smart_data, signals = await _finalize_quality_classify(
                result,
                client=client,
                name=name,
                domain=domain,
                signals=signals,
                query_context=query_context,
                smart_data=smart_data,
                is_seed=is_seed,
                industry_kws=industry_kws,
            )
        # Prefer the LLM's role (it read the actual site). company_function is derived
        # from the SEARCH PROMPT, so with focused discovery it is uniformly "manufacturer"
        # and must NOT override a real role (cert body, distributor, supplier, ...).
        if result.get("role") not in _VALID_ROLES:
            result["role"] = (
                _role_from_company_function(company_function)
                or _infer_role_from_name_domain(name, domain)
            )
        if signals.get("is_distributor") and result["role"] == "Manufacturer":
            result["role"] = "Distributor"
        elif signals.get("is_supplier") and result["role"] == "Manufacturer":
            if "export" in (company_function or "").lower() or "trad" in domain:
                result["role"] = "Supplier"
        from vendor_intel.pipeline.llm_meter import get_meter

        get_meter().add_classify()
        tag = " recall" if recall_mode else ""
        print(
            f"  [classify] {name[:35]} → relevant={result['is_relevant']} "
            f"role={result['role']} conf={result['confidence']:.2f}{tag}",
            flush=True,
        )
        return _pack_classify_result(result, smart_data, signals)
    except Exception as exc:
        print(f"  [classify] LLM failed for {name[:40]}: {exc}", flush=True)
        fb = (
            _rule_fallback_recall(company, smart_data, query_context)
            if recall_mode
            else _rule_fallback(company, smart_data, query_context)
        )
        if quality_mode:
            fb, smart_data, signals = await _finalize_quality_classify(
                fb,
                client=client,
                name=name,
                domain=domain,
                signals=signals,
                query_context=query_context,
                smart_data=smart_data,
                is_seed=is_seed,
                industry_kws=industry_kws,
            )
            return _pack_classify_result(fb, smart_data, signals)
        elif not recall_mode:
            fb["confidence"] = min(float(fb["confidence"]), 0.4)
        return fb
