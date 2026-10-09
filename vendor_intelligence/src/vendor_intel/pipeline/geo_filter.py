"""Strict geography filter for region-scoped queries.

Drops companies that are *clearly* headquartered outside the requested geography
(e.g. a Chinese or Australian company in a 'Europe' or 'US' landscape). Conservative
by design: it only rejects when there is positive evidence of a different country
(HQ statement or a country-code TLD) AND no evidence of presence in the target.
Companies with no geo signal are kept (we don't prune on absence). Seeds are exempt.
"""
from __future__ import annotations

import re
from typing import Any

# country-code TLD -> canonical country name
_CCTLD_COUNTRY: dict[str, str] = {
    "au": "australia", "cn": "china", "in": "india", "us": "united states",
    "uk": "united kingdom", "de": "germany", "fr": "france", "it": "italy",
    "es": "spain", "nl": "netherlands", "se": "sweden", "pl": "poland",
    "ca": "canada", "jp": "japan", "kr": "south korea", "tw": "taiwan",
    "br": "brazil", "mx": "mexico", "ru": "russia", "tr": "turkey",
    "ch": "switzerland", "at": "austria", "be": "belgium", "dk": "denmark",
    "fi": "finland", "no": "norway", "ie": "ireland", "pt": "portugal",
    "cz": "czech republic", "ae": "uae", "za": "south africa", "sg": "singapore",
}

# Region -> member countries (lowercase canonical names)
_EUROPE = {
    "spain", "france", "italy", "germany", "united kingdom", "netherlands",
    "sweden", "poland", "switzerland", "austria", "belgium", "denmark",
    "finland", "norway", "ireland", "portugal", "czech republic", "greece",
    "hungary", "romania", "slovakia", "slovenia", "croatia", "luxembourg",
    "estonia", "latvia", "lithuania", "bulgaria",
}
_REGION_COUNTRIES: dict[str, set[str]] = {
    "europe": _EUROPE,
    "eu": _EUROPE,
    "european union": _EUROPE,
    "north america": {"united states", "canada", "mexico"},
    "apac": {"china", "japan", "south korea", "india", "taiwan", "singapore", "australia"},
    "asia": {"china", "japan", "south korea", "india", "taiwan", "singapore"},
}

_COUNTRY_ALIASES: dict[str, str] = {
    "us": "united states", "u.s.": "united states", "u.s.a": "united states",
    "usa": "united states", "america": "united states",
    "uk": "united kingdom", "gb": "united kingdom", "britain": "united kingdom",
    "uae": "uae", "prc": "china",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower()).rstrip(".")


def resolve_target_countries(geo: str) -> set[str]:
    """Set of acceptable countries for the target geography. Empty = no constraint."""
    g = _norm(geo)
    if not g or g in ("global", "worldwide", "international", "all", "world"):
        return set()
    if g in _REGION_COUNTRIES:
        return set(_REGION_COUNTRIES[g])
    g = _COUNTRY_ALIASES.get(g, g)
    return {g}


_MULTI_CCTLD: tuple[tuple[str, str], ...] = (
    (".com.au", "au"), (".co.uk", "uk"), (".org.uk", "uk"), (".co.in", "in"),
    (".com.cn", "cn"), (".co.jp", "jp"), (".com.br", "br"), (".com.mx", "mx"),
    (".co.za", "za"), (".com.sg", "sg"), (".com.tw", "tw"), (".com.tr", "tr"),
)


def _country_from_domain(domain: str) -> str:
    dom = (domain or "").lower().strip().rstrip(".")
    for suf, cc in _MULTI_CCTLD:
        if dom.endswith(suf):
            return _CCTLD_COUNTRY.get(cc, "")
    m = re.search(r"\.([a-z]{2})$", dom)
    if m:
        # .us/.in/.de etc. — generic .com/.org/.net won't match the map
        return _CCTLD_COUNTRY.get(m.group(1), "")
    return ""


def infer_company_country(verdict: dict[str, Any], signals: dict[str, Any] | None) -> str:
    """Best guess of the company's home country, or '' if unknown."""
    sig = signals or verdict.get("signals") or {}
    hq = _norm(str(sig.get("hq_country") or ""))
    hq = _COUNTRY_ALIASES.get(hq, hq)
    if hq:
        # only trust a recognised country name
        known = set(_CCTLD_COUNTRY.values()) | {"united states", "united kingdom", "uae"}
        if hq in known or hq in _EUROPE:
            return hq
    domain = str(verdict.get("domain") or verdict.get("website") or "")
    return _country_from_domain(domain)


# Terms that indicate operating presence in a country (name + native name + ccTLD).
_PRESENCE_TERMS: dict[str, list[str]] = {
    "brazil": ["brazil", "brasil", ".com.br", ".br"],
    "spain": ["spain", "españa", "espana", ".es"],
    "germany": ["germany", "deutschland", ".de"],
    "france": ["france", ".fr"],
    "italy": ["italy", "italia", ".it"],
    "japan": ["japan", "日本", ".jp", ".co.jp"],
    "china": ["china", "中国", ".cn"],
    "india": ["india", ".in", ".co.in"],
    "united states": ["united states", "usa", "u.s.", "america", ".us"],
    "united kingdom": ["united kingdom", " uk ", "britain", "england", ".co.uk", ".uk"],
    "mexico": ["mexico", "méxico", ".mx"],
    "canada": ["canada", ".ca"],
    "australia": ["australia", ".com.au", ".au"],
}


def _operates_in_target(verdict: dict[str, Any], signals: dict[str, Any], targets: set[str]) -> bool:
    """True if the company shows operating presence in (or serves) any target country."""
    mentioned = " ".join(str(x) for x in (signals.get("mentioned_countries") or []))
    blob = " ".join(
        [
            str(verdict.get("operational_presence") or ""),
            str(verdict.get("company_summary") or ""),
            str(verdict.get("role_description") or ""),
            str(verdict.get("domain") or verdict.get("website") or ""),
            str(verdict.get("company") or ""),
            mentioned,
        ]
    ).lower()
    for country in targets:
        for term in _PRESENCE_TERMS.get(country, [country]):
            if term and term in blob:
                return True
    return False


def geo_mismatch_reason(
    verdict: dict[str, Any], signals: dict[str, Any] | None, target_geo: str
) -> str | None:
    """Drop a company only if it is clearly foreign AND shows no presence in the target.

    Keeps: companies HQ'd in the target, OR that operate/serve there (site mentions the
    country / native name / ccTLD), OR whose country is unknown. Drops: clearly foreign-HQ
    companies with no detected presence in the target geography. Seeds are always kept.
    """
    targets = resolve_target_countries(target_geo)
    if not targets:
        return None  # global / unconstrained
    if verdict.get("is_seed"):
        return None
    sig = signals or verdict.get("signals") or {}
    company_country = infer_company_country(verdict, sig)
    if company_country and company_country in targets:
        return None
    if _operates_in_target(verdict, sig, targets):
        return None  # serves / operates in the target geography → keep
    if company_country:  # clearly foreign-HQ and no detected presence in target
        return f"geo_mismatch:{company_country}"
    return None  # country unknown and no foreign signal → keep (benefit of the doubt)
