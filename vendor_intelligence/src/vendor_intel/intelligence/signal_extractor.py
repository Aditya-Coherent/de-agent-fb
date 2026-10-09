"""Rule-based signals from smart_crawl output (no LLM)."""
from __future__ import annotations

import re
from typing import Any

_MANUFACTURER = re.compile(
    r"\b(?:manufactur(?:e|ing|er)|production\s+facility|plant\b|factory\b|"
    r"GMP\b|bulk\s+drug|API\b|formulation)\b",
    re.I,
)
_DISTRIBUTOR = re.compile(
    r"\b(?:distribut(?:or|ion)|wholesale|logistics\s+partner|supply\s+chain|"
    r"dealer|stockist|C&F\b)\b",
    re.I,
)
_SUPPLIER = re.compile(
    r"\b(?:supplier|vendor|procurement|sourcing|raw\s+material)\b",
    re.I,
)
_SERVICES = re.compile(
    r"\b(?:consulting|advisory|services?\s+provider|SaaS|software\s+solution)\b",
    re.I,
)
_PRODUCTS = re.compile(
    r"\b(?:products?|solutions?|portfolio|catalog|our\s+range|offerings?)\b",
    re.I,
)
_FACADE_PRODUCTS = re.compile(
    r"\b(?:acp|aluminium\s+composite|aluminum\s+composite|rainscreen|curtain\s*wall|"
    r"facade\s+panel|ventilated\s+facade|cladding\s+system|composite\s+panel)\b",
    re.I,
)
_DIGITAL_SIGNAGE_PRODUCTS = re.compile(
    r"\b(?:digital\s+signage|signage\s+cms|content\s+management\s+system|"
    r"media\s+player|display\s+network|led\s+display|interactive\s+kiosk|"
    r"video\s+wall|dooh|signage\s+software|signage\s+platform|narrowcasting)\b",
    re.I,
)
_TECH_PROVIDER = re.compile(
    r"\b(?:saas|software\s+platform|cloud\s+platform|cms\b|sdk\b|api\s+integration)\b",
    re.I,
)

# Major countries + EU members + common regions for presence detection
_COUNTRY_NAMES: tuple[str, ...] = (
    "United States",
    "USA",
    "United Kingdom",
    "UK",
    "Germany",
    "France",
    "Italy",
    "Spain",
    "Netherlands",
    "Belgium",
    "Poland",
    "Turkey",
    "Ukraine",
    "Switzerland",
    "Austria",
    "Sweden",
    "Norway",
    "Denmark",
    "Finland",
    "Ireland",
    "Portugal",
    "Greece",
    "Czech Republic",
    "Romania",
    "Hungary",
    "India",
    "China",
    "Japan",
    "South Korea",
    "Brazil",
    "Mexico",
    "Canada",
    "Australia",
    "Singapore",
    "UAE",
    "United Arab Emirates",
    "Saudi Arabia",
    "Israel",
    "South Africa",
    "Taiwan",
    "Thailand",
    "Vietnam",
    "Indonesia",
    "Malaysia",
    "Russia",
    "Egypt",
    "Nigeria",
    "Bulgaria",
    "Croatia",
    "Cyprus",
    "Estonia",
    "Latvia",
    "Lithuania",
    "Luxembourg",
    "Malta",
    "Slovakia",
    "Slovenia",
    "Iceland",
    "Europe",
    "Nordics",
    "Benelux",
    "Scandinavia",
)

_HQ_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"headquartered in ([^.;\n]{3,80})", re.I),
    re.compile(r"head office in ([^.;\n]{3,80})", re.I),
    re.compile(r"headquarters in ([^.;\n]{3,80})", re.I),
    re.compile(r"\bbased in ([^.;\n]{3,80})", re.I),
    re.compile(r"\bhq in ([^.;\n]{3,80})", re.I),
    re.compile(r"head office:\s*([^.;\n]{3,80})", re.I),
)


_SCHEMA_KEYS = frozenset(
    {
        "name",
        "website",
        "products",
        "services",
        "summary",
        "description",
        "about",
        "tagline",
        "overview",
        "business",
        "company",
        "location",
        "intel",
        "javascript",
        "appears",
        "disabled",
        "search",
        "false",
        "true",
        "null",
        "data",
        "source",
        "error",
        "pages",
        "domain",
        "segments",
        "industries",
    }
)


def _collect_value_text(smart_data: dict[str, Any]) -> str:
    """Natural-language crawl text — values only, not JSON field names."""
    if not smart_data:
        return ""
    parts: list[str] = []
    data = smart_data.get("data") or {}
    if isinstance(data, dict):
        for section in ("company", "business", "location", "intel"):
            block = data.get(section)
            if isinstance(block, dict):
                for key, val in block.items():
                    if str(key).lower() in _SCHEMA_KEYS:
                        continue
                    if isinstance(val, str) and len(val.strip()) >= 8:
                        parts.append(val.strip())
                    elif isinstance(val, list):
                        parts.extend(
                            str(x).strip()
                            for x in val[:14]
                            if len(str(x).strip()) >= 3
                        )
            elif isinstance(block, str) and len(block.strip()) >= 8:
                parts.append(block.strip())
        intel = data.get("intel")
        if isinstance(intel, dict):
            for key in ("summary", "synthesis"):
                val = intel.get(key)
                if val and str(val).strip():
                    parts.append(str(val).strip())
    for page in smart_data.get("pages") or []:
        if isinstance(page, dict) and page.get("text"):
            parts.append(str(page["text"])[:8000])
    dom = str(smart_data.get("domain") or "").strip()
    if dom:
        parts.append(dom)
    return " ".join(parts)


def _flatten_smart_data(smart_data: dict[str, Any]) -> str:
    if not smart_data:
        return ""
    if smart_data.get("error") and not smart_data.get("data") and not smart_data.get("pages"):
        return ""
    text = _collect_value_text(smart_data)
    if len(text) >= 40:
        return text
    parts: list[str] = []
    data = smart_data.get("data") or {}
    if isinstance(data, dict):
        for section in ("company", "business", "location", "intel"):
            block = data.get(section)
            if isinstance(block, dict):
                parts.append(str(block))
            elif block:
                parts.append(str(block))
    return " ".join(parts)


def _filtered_keywords(words: list[str]) -> list[str]:
    from vendor_intel.pipeline.csv_fields import filter_product_keywords

    return filter_product_keywords(words)


def _keyword_list(blob: str, limit: int = 24) -> list[str]:
    words = re.findall(r"[a-z]{4,}", blob.lower())
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        if w in seen or w in _SCHEMA_KEYS:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= limit:
            break
    return out


def _extract_hq_country(text: str) -> str:
    for pat in _HQ_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        loc = m.group(1).strip()
        loc = re.sub(r"\s{2,}", " ", loc)
        loc = loc.split(",")[0].strip()
        if len(loc) >= 3:
            return loc[:60]
    return ""


def _extract_mentioned_countries(text: str) -> list[str]:
    low = text.lower()
    found: list[str] = []
    seen: set[str] = set()
    # Longer names first to avoid partial matches
    for country in sorted(_COUNTRY_NAMES, key=len, reverse=True):
        key = country.lower()
        if key in seen:
            continue
        if re.search(rf"\b{re.escape(key)}\b", low):
            seen.add(key)
            found.append(country)
        if len(found) >= 12:
            break
    return found


def extract_signals(smart_data: dict[str, Any], *, industry_keywords: list[str] | None = None) -> dict[str, Any]:
    """
    Extract rule-based signals from smart_crawl output.

    Returns:
        has_products, is_manufacturer, is_distributor, is_supplier,
        is_services, keywords, industry_match, hq_country, mentioned_countries
    """
    raw_blob = _flatten_smart_data(smart_data or {})
    blob = raw_blob.lower()
    if len(blob) < 40:
        return {
            "has_products": False,
            "is_manufacturer": False,
            "is_distributor": False,
            "is_supplier": False,
            "is_services": False,
            "keywords": [],
            "industry_match": False,
            "hq_country": "",
            "mentioned_countries": [],
        }

    from vendor_intel.pipeline.participant_domains import filter_industry_match_terms

    industry_keywords = filter_industry_match_terms(
        [str(k).strip().lower() for k in (industry_keywords or []) if str(k).strip()]
    )
    industry_match = False
    for k in industry_keywords:
        if len(k) < 4:
            continue
        if k in blob or k.replace("-", " ") in blob or k.replace(" ", "-") in blob:
            industry_match = True
            break

    ds_products = bool(_DIGITAL_SIGNAGE_PRODUCTS.search(blob))
    has_products = (
        bool(_PRODUCTS.search(blob))
        or bool(_FACADE_PRODUCTS.search(blob))
        or ds_products
    )
    return {
        "has_products": has_products,
        "is_manufacturer": bool(_MANUFACTURER.search(blob)),
        "is_distributor": bool(_DISTRIBUTOR.search(blob)),
        "is_supplier": bool(_SUPPLIER.search(blob)),
        "is_services": bool(_SERVICES.search(blob)),
        "is_technology_provider": bool(_TECH_PROVIDER.search(blob)) or ds_products,
        "digital_signage_products": ds_products,
        "keywords": _filtered_keywords(_keyword_list(blob)),
        "industry_match": industry_match,
        "hq_country": _extract_hq_country(raw_blob),
        "mentioned_countries": _extract_mentioned_countries(raw_blob),
    }
