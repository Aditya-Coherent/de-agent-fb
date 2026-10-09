"""
Geography signal sets for Phase 3 validation gate.
Scope-driven: the target geography comes from scope.get("geographies"),
never hardcoded in validation logic.
"""
from __future__ import annotations

from typing import Iterable

# CHANGED: dynamic geo gate — single source of truth for country/city/regulatory signals
_GEO_SIGNAL_MAP: dict[str, set[str]] = {
    "india": {
        "india", "indian", "bharat",
        "mumbai", "delhi", "bangalore", "bengaluru", "hyderabad", "chennai",
        "kolkata", "pune", "ahmedabad", "surat", "jaipur", "lucknow",
        "kanpur", "nagpur", "indore", "thane", "bhopal", "visakhapatnam",
        "patna", "vadodara", "ghaziabad", "ludhiana", "agra", "nashik",
        "faridabad", "meerut", "rajkot", "coimbatore", "madurai", "chandigarh",
        "noida", "gurugram", "gurgaon", "navi mumbai", "ranchi", "srinagar",
        "aurangabad", "jodhpur", "raipur", "kota",
        "gstin", "gst no", "gst number", "gst in",
        "cin:", "cin no", "u74", "l74", "u67",
        "ministry of corporate affairs", "mca21", " mca ",
        "registered under companies act",
        "incorporated in india",
        "sebi registered", "sebi reg",
        "rbi licence", "rbi license",
        "msme registered", "udyam",
        # Currency / pricing — strongest India signal on product pages
        "₹", "inr", "rs.", "rupee", "rupees",
        # Common India-localized URL path segments
        "/in/", "/en-in", "/in-en",
        ".in",
    },
    "united states": {
        "united states", "usa", "u.s.a", "u.s.", "america", "american",
        "new york", "los angeles", "chicago", "houston", "phoenix",
        "philadelphia", "san antonio", "san diego", "dallas", "san jose",
        "austin", "jacksonville", "fort worth", "columbus", "charlotte",
        "san francisco", "indianapolis", "seattle", "denver", "boston",
        "nashville", "portland", "las vegas", "miami", "atlanta",
        "incorporated in delaware", "inc.", " llc", " corp.",
        "sec filing", "ein:", "employer identification",
        "nasdaq", "nyse listed",
        ".us",
    },
    "united kingdom": {
        "united kingdom", "uk", "great britain", "britain", "england",
        "scotland", "wales",
        "london", "birmingham", "manchester", "leeds", "glasgow",
        "liverpool", "bristol", "cardiff", "edinburgh", "sheffield",
        "bradford", "leicester", "coventry", "belfast", "nottingham",
        "companies house", "registered in england", "registered in scotland",
        "company number", "vat no", "vat number", "uk vat",
        "plc", "ltd", "limited liability",
        ".co.uk", ".uk",
    },
    "china": {
        "china", "chinese", "prc", "people's republic",
        "beijing", "shanghai", "shenzhen", "guangzhou", "chengdu",
        "wuhan", "chongqing", "tianjin", "nanjing", "hangzhou",
        "xi'an", "xian", "suzhou", "qingdao", "zhengzhou",
        "registered in china", "中国", "people's republic of china",
        "工商", "营业执照",
        ".cn",
    },
    "germany": {
        "germany", "german", "deutschland",
        "berlin", "hamburg", "munich", "münchen", "cologne", "köln",
        "frankfurt", "stuttgart", "düsseldorf", "dortmund", "essen",
        "leipzig", "bremen", "hannover", "nuremberg", "nürnberg",
        "gmbh", "ag ", " kg ", "e.v.", "registered in germany",
        "handelsregister", "ust-id", "vat de",
        "amtsgericht",
        ".de",
    },
    "australia": {
        "australia", "australian",
        "sydney", "melbourne", "brisbane", "perth", "adelaide",
        "gold coast", "canberra", "newcastle", "wollongong", "hobart",
        "abn:", "acn:", "australian business number",
        "asic registered", "pty ltd", "pty. ltd.",
        ".com.au", ".au",
    },
    "canada": {
        "canada", "canadian",
        "toronto", "montreal", "vancouver", "calgary", "edmonton",
        "ottawa", "winnipeg", "quebec city", "hamilton", "kitchener",
        "incorporated in canada", "registered in ontario",
        "cra number", "gst/hst",
        ".ca",
    },
    "uae": {
        "uae", "united arab emirates", "emirates",
        "dubai", "abu dhabi", "sharjah", "ajman", "fujairah", "ras al khaimah",
        "trn:", "trade license", "free zone", "freezone",
        "dafza", "jafza", "difc", "adgm",
        ".ae",
    },
}

_ALIASES: dict[str, str] = {
    "us": "united states",
    "usa": "united states",
    "uk": "united kingdom",
    "gb": "united kingdom",
    "cn": "china",
    "de": "germany",
    "au": "australia",
    "ca": "canada",
    "in": "india",
}


def _normalize_geo(geo: str) -> str:
    return geo.lower().strip().rstrip(".")


def _resolve_geo_key(geo: str) -> str:
    """Map display geo strings like 'Nova Scotia, Canada' → canada."""
    normalized = _normalize_geo(geo)
    if normalized in _GEO_SIGNAL_MAP:
        return normalized
    if normalized in _ALIASES:
        return _ALIASES[normalized]
    if "," in geo:
        for part in reversed([p.strip() for p in geo.split(",")]):
            key = _normalize_geo(part)
            if key in _GEO_SIGNAL_MAP:
                return key
            if key in _ALIASES:
                return _ALIASES[key]
    return normalized


def _fallback_signals(geo: str) -> set[str]:
    """Fallback for countries not in _GEO_SIGNAL_MAP — never raises."""
    resolved = _resolve_geo_key(geo)
    if resolved in _GEO_SIGNAL_MAP:
        return set(_GEO_SIGNAL_MAP[resolved])
    normalized = _normalize_geo(geo)
    return {normalized} if normalized else set()


def get_geo_signals(geographies: Iterable[str]) -> set[str]:
    """
    Combined geography signals for all target geographies from scope.
    Example: scope.get("geographies") → ["India"]
    """
    combined: set[str] = set()
    for geo in geographies:
        if not geo or str(geo).strip().lower() == "global":
            continue
        key = _resolve_geo_key(str(geo))
        signals = _GEO_SIGNAL_MAP.get(key) or _fallback_signals(str(geo))
        combined.update(signals)
    return combined


def check_geo_match(
    scraped_text: str,
    domain: str,
    signals: set[str],
    *,
    url: str = "",
) -> tuple[bool, str]:
    """
    Return (matched, matched_signal).

    Checks (in order):
    1. Signal found in scraped text (case-insensitive)
    2. TLD / domain suffix match (e.g. .in for India)
    3. URL path contains country segment (e.g. /in/, /en-in)
    """
    if not signals:
        return False, ""

    text_lower = (scraped_text or "").lower()
    domain_lower = (domain or "").lower()
    url_lower = (url or domain or "").lower()

    for signal in signals:
        # Text match
        if signal in text_lower:
            return True, signal
        # Domain TLD match (.in, .co.uk, etc.)
        if signal.startswith(".") and not "/" in signal and domain_lower.endswith(signal):
            return True, f"domain={domain_lower}"
        # URL path segment match (/in/, /en-in, etc.)
        # Also handle URLs that end without trailing slash (vivo.com/in, not vivo.com/in/)
        if signal.startswith("/"):
            sig_stripped = signal.rstrip("/")
            if signal in url_lower or url_lower.endswith(sig_stripped):
                return True, f"url_path={sig_stripped}"

    return False, ""


def geo_evidence_snippet(matched_signal: str, geo: str) -> str:
    if matched_signal.startswith("domain="):
        return f"Country-specific domain detected ({matched_signal}) for {geo}."
    regulatory_signals = (
        "gstin", "gst no", "cin:", "mca21", "sebi", "msme",
        "companies house", "abn:", "acn:", "trn:", "gmbh", "plc",
    )
    if any(r in matched_signal for r in regulatory_signals):
        return (
            f"Country-specific regulatory signal '{matched_signal}' "
            f"found — confirms presence in {geo}."
        )
    return f"Geography signal '{matched_signal}' found — confirms presence in {geo}."
