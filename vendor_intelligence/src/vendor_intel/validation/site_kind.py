"""
Classify whether a candidate is a product company vs media/blog/directory/article noise.
Scope-agnostic heuristics — no hardcoded allowlists of brand names.

Key public API:
  classify_domain(domain) -> str   e.g. "media", "education", "aggregator", "finance", "company"
  is_non_product_site(domain, ...) -> bool
"""
from __future__ import annotations

import re
from functools import lru_cache


# ---------------------------------------------------------------------------
# Pattern-based domain classifier
# ---------------------------------------------------------------------------

_DOMAIN_CLASS_RULES: list[tuple[str, list[str]]] = [
    # Order matters: first match wins
    ("media", [
        "news", "blog", "magazine", "mag", "times", "express", "herald",
        "post", "daily", "khabar", "affairs", "media", "journal",
        "press", "report", "reporter", "dispatch", "chronicle",
        "insider", "digest", "gazette", "wire",
        "marketresearch", "marketinsights", "futuremarket", "grandview",
        "factmr", "zionmarket", "straitsresearch", "credenceresearch",
        "cognitivemarket", "mnemonicsresearch", "markwideresearch",
        "valuespectrum", "mordorintelligence", "foodtechbiz",
    ]),
    ("education", [
        "learn", "academy", "course", "training", "edu", "institute",
        "school", "university", "college", "tutor", "tutorial",
        "intellipaat", "simplilearn", "edureka", "coursera", "udemy",
        "geeksforgeeks", "w3schools",
    ]),
    ("finance", [
        "moneyworks", "invest", "stock", "finance", "fintech", "finserv",
        "screener", "trendlyne", "tickertape", "wallet", "bank", "trading",
        "mutual", "portfolio", "nse", "bse", "sensex",
    ]),
    ("aggregator", [
        "companydata", "superbcompanies", "companieslist", "ensun",
        "crunchbase", "tracxn", "zaubacorp", "zauba", "mca21",
        "indiamart", "tradeindia", "justdial", "yellowpages",
        "importyeti", "seair", "eximpedia", "volza",
        "capterra", "g2", "techjockey", "softwaresuggest", "getapp",
        "clutch", "goodfirms",
    ]),
    ("ecommerce", [
        "amazon", "flipkart", "snapdeal", "myntra", "shopify",
        "croma", "reliance", "meesho", "ajio",
    ]),
    ("startup_news", [
        "inventiva", "inc42", "entrackr", "startupstory", "yourstory",
        "startupindia", "thenextweb",
    ]),
    ("seo_blog", [
        "positioniseverything", "techbloat", "bloat", "seo",
        "digitalmarketing", "rankmath", "ahrefs", "moz",
    ]),
    ("review", [
        "review", "compare", "comparison", "versus", "bestof", "topreview",
        "pcmag", "techradar", "wired", "cnet", "verge", "engadget",
        "gsmarena", "91mobiles", "gadgets360",
    ]),
    ("directory", [
        "directory", "listing", "classified", "helpline", "finder",
        "locator", "yellowpage", "sulekha", "olx",
    ]),
]

# Exact domain → class overrides (for domains that don't contain a keyword signal)
_DOMAIN_EXACT_CLASS: dict[str, str] = {
    "intellipaat.com": "education",
    "simplilearn.com": "education",
    "edureka.co": "education",
    "moneyworks4me.com": "finance",
    "screener.in": "finance",
    "bajajfinserv.in": "finance",
    "positioniseverything.net": "seo_blog",
    "techbloat.com": "seo_blog",
    "companydata.com": "aggregator",
    "superbcompanies.com": "aggregator",
    "ensun.io": "aggregator",
    "seair.co.in": "aggregator",
    "inventiva.co.in": "startup_news",
    "entrackr.com": "startup_news",
    "inc42.com": "startup_news",
    "techjockey.com": "aggregator",
    "capterra.com": "aggregator",
    "g2.com": "aggregator",
    "crunchbase.com": "aggregator",
    "tracxn.com": "aggregator",
    "indiamart.com": "aggregator",
    "justdial.com": "directory",
    "tradeindia.com": "directory",
    "zauba.com": "aggregator",
    "zaubacorp.com": "aggregator",
    "simplywall.st": "finance",
    "gsmarena.com": "review",
    "91mobiles.com": "review",
    "gadgets360.com": "review",
    "digit.in": "review",
    "bgr.in": "review",
    "techradar.com": "review",
    "croma.com": "ecommerce",
}

# Classes that indicate a site is NOT a product/service company
_NON_COMPANY_CLASSES = frozenset({
    "media", "education", "aggregator", "ecommerce",
    "startup_news", "seo_blog", "review", "directory", "finance",
})


@lru_cache(maxsize=1024)
def classify_domain(domain: str) -> str:
    """Classify a domain into a site category.

    Returns one of: media, education, finance, aggregator, ecommerce,
    startup_news, seo_blog, review, directory, company (default).
    """
    if not domain:
        return "unknown"
    low = domain.lower().strip().removeprefix("www.").split(":")[0]
    # Strip TLD for keyword matching
    base = low.split(".")[0]

    # Check exact overrides first
    if low in _DOMAIN_EXACT_CLASS:
        return _DOMAIN_EXACT_CLASS[low]

    # Pattern-based classification on base label + full domain
    for cls, keywords in _DOMAIN_CLASS_RULES:
        for kw in keywords:
            if kw in base or kw in low:
                return cls

    return "company"

# CHANGED: phase3 quality fix — article/listicle titles mistaken as company names
_ARTICLE_TITLE_NAME = re.compile(
    r"^(?:top|best|leading|biggest|major|complete\s+list|list\s+of|full\s+list|"
    r"the\s+top\s*$|"
    r"\d+\s+(?:best|top)|how\s+to|what\s+are|guide\s+to|"
    r".*\b(?:in\s+india|in\s+the\s+us|in\s+uk)\s*$|"
    r".*\b(?:archives?|classifieds?|manufacturers?\s+companies)\s*$|"
    r".*\b(?:brands?\s+in\s+india|companies\s+in\s+india)\s*$|"
    r".*\bexport\s+companies\s*$|"
    r".*\biso\s+certified\b|"
    r".*\bthird\s+party\s+manufacturing\b|"
    r".*\bpharma\s+distributors?\s*$|"
    r".*\b(?:distributors?\s+list|dealers?\s+list|suppliers?\s+list)\s*$|"
    r".*\b(?:pharmaceutical|medical)\s+(?:dealers?|distributors?|suppliers?)\s*$|"
    r".*\bmarket\s+the\s*$|"
    r".*\bexplore\s*$|"
    r"inadequate\s+service|retail\s+stores$|mobiles?\s+phones?\s+classifieds)",
    re.I,
)

# Domain label patterns (base domain before TLD)
_NON_PRODUCT_DOMAIN_PARTS = re.compile(
    r"(?:^|[.-])(?:news|blog|magazine|mag|talk|times|express|herald|post|daily|"
    r"khabar|affairs|helpline|classified|directory|companies|price|compare|"
    r"review|reviews|forum|community|wiki|tips|lifestyle|fashion|seo|marketing|"
    r"digital|agency|soft|infos|article|artical|nubia\s*page|telecom\s*talk|"
    r"traffic\s*tail|brandz|genx|satlife|currentaffairs|indiancompanies|"
    r"priceindia|justhelpline|internationalkhabar|"
    # Finance / stock / investment platforms
    r"moneyworks|moneyw|stockanalysis|investopedia|finshots|"
    # Education / training / course platforms
    r"intellipaat|udemy|coursera|simplilearn|edureka|"
    # Blog / content / SEO sites
    r"positioniseverything|techbloat|bloat|"
    # Generic company data / aggregators
    r"companydata|superbcompanies|superb|companieslist|"
    # Startup / funding news sites
    r"inventiva|startupstory|entrackr|inc42|"
    # Import/export data providers
    r"seair|zauba|eximpedia|volza|importyeti)(?:[.-]|$)",
    re.I,
)

_NON_PRODUCT_DOMAIN_EXACT = frozenset(
    {
        "croma.com",
        "tradetu.com",
        "indiamart.com",
        "justdial.com",
        "tradeindia.com",
        "zauba.com",
        "zaubacorp.com",
        "simplywall.st",
        "crunchbase.com",
        "gsmarena.com",
        "91mobiles.com",
        "gadgets360.com",
        "digit.in",
        "bgr.in",
        "techradar.com",
        "telecomtalk.info",
        "stylesatlife.com",
        "vastinfos.com",
        "brandzmagazine.com",
        "indiacurrentaffairs.com",
        "internationalkhabar.com",
        "justhelpline.com",
        "priceindia.com",
        "nubiapage.com",
        "traffictail.com",
        "genxsoft.com",
        "articalize.com",
        "indiancompanies.in",
        # Finance / investment platforms
        "moneyworks4me.com",
        "moneycontrol.com",
        "screener.in",
        "trendlyne.com",
        "tickertape.in",
        # Education / training platforms
        "intellipaat.com",
        "simplilearn.com",
        "edureka.co",
        "coursera.org",
        "udemy.com",
        # Blog / content / SEO sites
        "positioniseverything.net",
        "techbloat.com",
        # Company data / aggregator / directory sites
        "companydata.com",
        "superbcompanies.com",
        "companieslist.in",
        "ensun.io",
        "techjockey.com",
        # Startup / tech news sites
        "inventiva.co.in",
        "entrackr.com",
        "inc42.com",
        # Import-export data
        "seair.co.in",
        "eximpedia.app",
        # Software marketplaces
        "capterra.com",
        "g2.com",
        "softwaresuggest.com",
        "getapp.com",
    }
)

_NON_PRODUCT_TEXT = re.compile(
    r"\b(?:subscribe\s+to\s+newsletter|read\s+more|posted\s+on|written\s+by|"
    r"all\s+rights\s+reserved\s*\|\s*news|breaking\s+news|latest\s+news|"
    r"price\s+comparison|compare\s+prices|classified\s+ads|"
    r"business\s+magazine|lifestyle\s+blog|digital\s+marketing\s+agency|"
    r"community\s+forum|user\s+forum|tag\s*:|category\s*:|"
    r"archives?\s+tag|open\s+access\s+journal|"
    r"online\s+(?:course|training|certification|learning)|"
    r"enroll\s+now|batch\s+starting|placement\s+assistance|"
    r"stock\s+(?:analysis|screener|watchlist)|"
    r"mutual\s+fund|share\s+price|nse\s+bse\s+stock|"
    r"import\s+export\s+data|shipment\s+data|trade\s+data\s+provider)\b",
    re.I,
)

_GENERIC_CANONICAL_NAMES = frozenset(
    {
        "mobiles",
        "mobile phones",
        "mobile phone",
        "retail stores",
        "retail store",
        "phones",
        "phone",
        "brands",
        "brand",
        "companies",
        "company",
        "stores",
        "store",
    }
)

_CORPORATE_SUFFIX = re.compile(
    r"\b(?:ltd\.?|limited|inc\.?|corp\.?|corporation|plc|gmbh|pvt\.?\s*ltd|"
    r"llp|industries|electronics|technologies|telecom)\b",
    re.I,
)


def is_article_title_name(name: str) -> bool:
    """True when the 'company name' is really a page/article title."""
    t = (name or "").strip()
    if len(t) < 4:
        return False
    if _ARTICLE_TITLE_NAME.search(t):
        return True
    words = t.split()
    if len(words) >= 5 and not _CORPORATE_SUFFIX.search(t):
        return True
    if len(words) >= 4 and re.search(
        r"\b(?:top|best|leading|manufacturers?|brands?|companies|list)\b", t, re.I
    ):
        return True
    return False


def is_non_product_domain(domain: str) -> bool:
    """True when domain belongs to a non-company site class (media/edu/aggregator etc.)."""
    if not domain:
        return False
    # Use the pattern classifier — faster and more maintainable than exact lists
    if classify_domain(domain) in _NON_COMPANY_CLASSES:
        return True
    # Also run the legacy regex for backwards compatibility
    low = domain.lower().strip().removeprefix("www.")
    if low in _NON_PRODUCT_DOMAIN_EXACT:
        return True
    if any(low == d or low.endswith("." + d) for d in _NON_PRODUCT_DOMAIN_EXACT):
        return True
    base = low.split(".")[0]
    if _NON_PRODUCT_DOMAIN_PARTS.search(base) or _NON_PRODUCT_DOMAIN_PARTS.search(low):
        return True
    return False


def name_domain_mismatch(name: str, domain: str) -> bool:
    """
    True when canonical name does not match the site brand (e.g. 'Mobiles' on croma.com).
    """
    if not name or not domain:
        return False
    low_name = name.lower().strip()
    if low_name in _GENERIC_CANONICAL_NAMES:
        return True
    base = domain.lower().split(".")[0].replace("-", "")
    compact = re.sub(r"[^a-z0-9]", "", low_name)
    if len(compact) < 4:
        return True
    if compact in base or base in compact:
        return False
    # First token should often appear in domain for single-brand sites
    first = re.sub(r"[^a-z0-9]", "", low_name.split()[0])
    if len(first) >= 4 and first not in base:
        if len(low_name.split()) <= 2 and not _CORPORATE_SUFFIX.search(name):
            return True
    return False


def is_non_product_site(
    domain: str,
    *,
    title: str = "",
    text: str = "",
    name: str = "",
) -> bool:
    """
    True for news/blog/directory/price-compare/marketing sites — not handset OEMs etc.
    """
    if is_non_product_domain(domain):
        return True
    if name and is_article_title_name(name):
        return True
    if name and name_domain_mismatch(name, domain):
        return True
    blob = f"{title} {text}".strip()
    if len(blob) >= 40 and _NON_PRODUCT_TEXT.search(blob):
        return True
    return False


def verification_cap_for_site(
    name: str,
    domain: str,
    title: str,
    snippet: str,
    *,
    proposed_score: float,
    proposed_reason: str,
) -> tuple[float, str]:
    """
    Cap verification confidence when domain match is a non-product site.
    """
    if proposed_reason != "official_domain_match" and proposed_score < 0.8:
        if is_non_product_site(domain, title=title, text=snippet, name=name):
            return min(proposed_score, 0.22), "non_product_site"
        return proposed_score, proposed_reason

    if is_non_product_site(domain, title=title, text=snippet, name=name):
        return 0.18, "non_product_site_domain_match"
    if is_article_title_name(name):
        return 0.12, "article_title_not_company"
    if name_domain_mismatch(name, domain):
        return 0.15, "name_domain_mismatch"
    return proposed_score, proposed_reason
