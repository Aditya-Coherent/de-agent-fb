"""Turn search hits into company names — filter listicles/junk; extract from domains and snippets."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from vendor_intel.models import DiscoveryHit
from vendor_intel.utils.domains import domain_from_url, normalize_name

_LISTICLE_DOMAINS = frozenset(
    {
        # --- market-research / aggregator / news / reference SOURCE domains (mined, never companies) ---
        "fortunebusinessinsights.com",
        "marketsandmarkets.com",
        "grandviewresearch.com",
        "businessresearchinsights.com",
        "marketsandata.com",
        "datahorizzonresearch.com",
        "meticulousresearch.com",
        "precedenceresearch.com",
        "alliedmarketresearch.com",
        "futuremarketinsights.com",
        "marketresearchfuture.com",
        "researchandmarkets.com",
        "cbinsights.com",
        "owler.com",
        "crunchbase.com",
        "statista.com",
        "tracxn.com",
        "g2.com",
        "capterra.com",
        "eoportal.org",
        "everythingrf.com",
        "satnow.com",
        "ieeexplore.ieee.org",
        "ieee.org",
        "mdpi.com",
        "researchgate.net",
        "wikipedia.org",
        "linkedin.com",
        "agenceecofin.com",
        "euractiv.com",
        "spaceconnectonline.com.au",
        "maritime-executive.com",
        "ictworks.org",
        "govtribe.com",
        "cbinsights.com",
        # --- existing ---
        "globaldata.com",
        "chemxpert.com",
        "ventuspharma.com",
        "ibef.org",
        "indiamart.com",
        "justdial.com",
        "groww.in",
        "mordorintelligence.com",
        "imarcgroup.com",
        "vendorlist.in",
        "careers360.com",
        "sharescart.com",
        "web.stockedge.com",
        "6wresearch.com",
        "gkgigs.com",
        "medindia.net",
        "toppharmacompanies.com",
        "pharmahopers.com",
        "ernstpharmacia.com",
        "jamkaspharma.com",
        "hcareindia.com",
        "bendichealthcare.in",
        "curavaxpharma.com",
        "clinilaunchresearch.in",
        # CHANGED: accuracy fix — tech/cyber/general listicle domains
        "positioniseverything.net",
        "techbloat.com",
        "moneyworks4me.com",
        "companydata.com",
        "superbcompanies.com",
        "intellipaat.com",
        "inventiva.co.in",
        "bajajfinserv.in",
        "seair.co.in",
        "ensun.io",
        "techjockey.com",
        "capterra.com",
        "g2.com",
        "softwaresuggest.com",
        "getapp.com",
        "clutch.co",
        "goodfirms.co",
        "inc42.com",
        "entrackr.com",
        "crunchbase.com",
        "tracxn.com",
        "internationalkhabar.com",
        "indiacurrentaffairs.org",
        "telecomtalk.info",
        "nubiapage.com",
        "genxsoft.info",
        "brandzmagazine.com",
        "stylesatlife.com",
        "traffictail.com",
        "articalize.com",
        "indiancompanies.in",
        "analyticsinsight.net",
        "businessworld.in",
        "cybersecurityventures.com",
        "cyberscoop.com",
        "securitymagazine.com",
        "darkreading.com",
        "helpnetsecurity.com",
        "infosecurity-magazine.com",
    }
)

_JUNK_DOMAIN_PARTS = (
    "wikipedia.org/wiki/category",
    "linkedin.com/pulse",
    "linkedin.com/posts",
    "britannica.com",
    "money.usnews.com",
    "jagranjosh.com",
    "grokipedia.com",
    "statista.com",
    "sayellow.com",
    "scribd.com",
    "quora.com",
    "youtube.com",
    "facebook.com",
    "instagram.com",
    "pinterest.com",
    "medium.com",
    "reddit.com",
    "investopedia.com",
    "globalhealthcaremagazine.com",
    "startuptalky.com",
    "ehealth.eletsonline.com",
    "jamkaspharma.com/top-",
    "cureton.in/top-",
    "theonpharma.com/top-",
    "mediquestpharma.org/top-",
    "pharmaadda.in/list-",
    "merriam-webster.com",
    "dictionary.com",
    "cambridge.org/dictionary",
    "fda.gov",
    "usp.org",
    "mdpi.com/journal",
    "topsmarkets.com",
    "tophat.com",
)

_BLOCKED_DOMAINS = frozenset(
    {
        "fda.gov",
        "usp.org",
        "mdpi.com",
        "merriam-webster.com",
        "dictionary.cambridge.org",
        "en.wikipedia.org",
    }
)

_LISTICLE_TITLE = re.compile(
    r"^(?:top\s+\d+|best\s+\d+|\d+\s+best|\d+\s+top|list\s+of|full\s+list|"
    r"category:|strategies\s+for|how\s+to|what\s+is|guide\s+to|"
    r"pharmaceutical\s+industry$|^pharmaceutical$|^pharmaceuticals$|"
    r"indian\s+pharma\s+industry|pharma\s+industry\s+in|definition\s*&\s*meaning|"
    r"best\s+pharma|top\s+pharma|pharma\s+companies\s+in\s+india|"
    r"indian\s+pharmacy|pharmaceutical\s+companies\s+in\s+india|"
    r"companies\s+in\s+india\s+before|explore\s+indian|api\s+manufacturer\s+in\s+india|"
    r"market\s+capitalization|b2b\s+pharmaceutical|buy\s+|shop\s+|get\s+\d+%|"
    r"today'?s\s+top|latest\s+news|news\s+headlines|times\s+top\d+|"
    r"women'?s\s+tops?|men'?s\s+tops?|shirts\s+and\s+tops|trendy\s+tops|"
    r"designer\s+clothes|online\s+in\s+india$|online\s+from\s+top|"
    # CHANGED: accuracy fix — generic industry list patterns (any vertical)
    r"leading\s+\w+\s+companies|major\s+\w+\s+companies|"
    r"top\s+\w+\s+(?:firms|vendors|providers|companies|brands)|"
    r"\w+\s+companies\s+in\s+india|"
    r"\w+\s+companies\s+in\s+the\s+us|"
    r"\w+\s+market\s+(?:leaders|share|players)|"
    r"cybersecurity\s+companies|cyber\s+security\s+companies|"
    r"leading\s+cybersecurity|top\s+cybersecurity|best\s+cybersecurity|"
    r"smartphone\s+brands?|mobile\s+phone\s+brands?|"
    r"(?:in\s+india|in\s+the\s+us|in\s+uk|in\s+europe)\s*$)",
    re.I,
)

_FASHION_OR_RETAIL_TITLE = re.compile(
    r"\b(?:women'?s\s+tops?|men'?s\s+tops?|shirts\s+and\s+tops|fancy\s+girls|"
    r"buy\s+(?:shirts|tops|trendy)|shop\s+designer|myntra|nykaa\s+fashion|"
    r"zara\.com|westside|max\s+fashion|clothing|apparel|footwear)\b",
    re.I,
)

_PAGE_TITLE_NOT_COMPANY = re.compile(
    r"^(?:home|about\s+us|contact\s+us|products?|services?|login|register)\s*$",
    re.I,
)

_QUESTION_TITLE = re.compile(r"\?\s*$|^(?:who|what|which|why|how)\s+", re.I)

_JUNK_NAME_PATTERN = re.compile(
    r"\b(?:journal|open access|fda|food and drug|pharmacopeia|definition|"
    r"dictionary|delivery or pickup|open access journal)\b",
    re.I,
)

_COMPANY_PATTERNS = [
    re.compile(
        r"\b([A-Z][A-Za-z0-9&'.-]*(?:\s+[A-Z][A-Za-z0-9&'.-]*){0,5}\s*"
        r"(?:Pharma(?:ceuticals?)?|Laboratories|Limited|Ltd\.?|Inc\.?|Industries|"
        r"Healthcare|Corporation|Corp\.?|Group|Company))\b"
    ),
    re.compile(
        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\s+Pharma)\b"
    ),
    re.compile(r"\b(Dr\.?\s+[A-Z][A-Za-z]+(?:'s)?(?:\s+Laboratories)?)\b"),
    re.compile(
        r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}\s+(?:Limited|Ltd\.?))\b"
    ),
    # Technology/cybersecurity company suffixes
    re.compile(
        r"\b([A-Z][A-Za-z0-9&'.-]*(?:\s+[A-Z][A-Za-z0-9&'.-]*){0,4}\s*"
        r"(?:Technologies|Technology|Networks|Network|Systems|Security|"
        r"Solutions|Software|Labs|Labs\.|Cyber|InfoSec|Tech|"
        r"Services|Consulting|Intelligence|Analytics|Cloud|Digital))\b"
    ),
]

_GENERIC_TITLE_WORDS = frozenset(
    {
        "pharmaceutical",
        "pharmaceuticals",
        "pharma",
        "industry",
        "companies",
        "company",
        "manufacturers",
        "manufacturer",
        "leading",
        "global",
        "indian",
        "overview",
        "analysis",
        "market",
        "forecast",
        "top",
        "hat",
        "markets",
        # Cybersecurity category terms that are NOT company names
        "cybersecurity",
        "cyber security",
        "network security",
        "information security",
        "data security",
        "cloud security",
        "endpoint security",
        "managed security services",
        "managed security",
        "security solutions",
        "security services",
        "it security",
        "india",
        "global",
        "dealers",
        "dealer",
        "distributors",
        "distributor",
        "suppliers",
        "supplier",
        "equipment",
        "medical",
        "right",
        "list",
        "lists",
    }
)

# Category / listicle phrases — pattern-based (not per-run name lists)
_ROLE_TAIL_RE = re.compile(
    r"\b(?:distributors?|dealers?|suppliers?|exporters?|importers?|wholesalers?|"
    r"retailers?|manufacturers?|vendors?|providers?|companies|company|list|lists)\s*$",
    re.I,
)

_GENERIC_PHRASE_RE = re.compile(
    r"^(?:the\s+)?(?:top|best|leading|right|major|largest|updated)\b|"
    r"\b(?:list\s+of|full\s+list|complete\s+list|distributors?\s+list)\b|"
    r"^(?:india|global|indian|international)\s+(?:pharmaceutical|pharma|medical)\b",
    re.I,
)

_LEADING_MARKETING_RE = re.compile(
    r"^(?:trusted|leading|innovative|global|premier|reliable|renowned|"
    r"top|best|world(?:class|wide)?|award[\s-]?winning)\s+",
    re.I,
)

_CERTIFICATION_PHRASE_RE = re.compile(
    r"\b(?:who[\s-]?gmp|iso\s*\d+|gmp\s+certified|certified\s+pharma|"
    r"who\s+certified|fda\s+approved)\b",
    re.I,
)

_GEO_ONLY_NAMES = frozenset(
    {"india", "global", "indian", "worldwide", "international", "national"}
)

_CORPORATE_MARKERS_RE = re.compile(
    r"\b(?:ltd\.?|limited|pvt\.?|inc\.?|corp\.?|corporation|laboratories|laboratory|"
    r"pharma(?:ceuticals?)?|industries|healthcare|group|technologies|technology|"
    r"solutions|systems|networks|security|labs)\b",
    re.I,
)

_INDUSTRY_ONLY_WORDS = frozenset(
    _GENERIC_TITLE_WORDS
    | {
        "dealers",
        "dealer",
        "distributors",
        "distributor",
        "suppliers",
        "supplier",
        "exporters",
        "exporter",
        "importers",
        "importer",
        "wholesalers",
        "wholesaler",
        "retailers",
        "retailer",
        "equipment",
        "medical",
        "medicines",
        "medicine",
        "products",
        "right",
        "list",
        "lists",
        "official",
        "website",
    }
)

# Names like "Managed Security Services (MSSP)" or "Endpoint Security (EDR)"
# are category labels, not company names
_CATEGORY_ACRONYM_PATTERN = re.compile(
    r"^[\w\s]+\s*\([A-Z]{2,6}\)\s*$",  # "Word Words (ACRONYM)"
    re.I,
)

# Detect names that are pure industry category phrases (no proper noun)
_INDUSTRY_CATEGORY_ONLY = re.compile(
    r"^(?:managed\s+(?:security|detection|response)|"
    r"endpoint\s+(?:security|protection|detection)|"
    r"network\s+(?:security|monitoring|detection)|"
    r"cloud\s+(?:security|protection)|"
    r"identity\s+(?:management|access|protection)|"
    r"threat\s+(?:intelligence|detection|hunting)|"
    r"security\s+(?:operations?|information|event)|"
    r"zero\s+trust|"
    r"(?:siem|soar|edr|xdr|mdr|soc|mssp|msp|ndr|ueba|iam|pam|dlp))\s*$",
    re.I,
)

_OFFICIAL_SITE_TITLE = re.compile(
    r"^(.+?)\s*[\|:\-–—]\s*(?:official|home|homepage|welcome|leading|global|"
    r"largest|no\.?\s*1|#1|pharmaceutical)",
    re.I,
)


@dataclass
class ExtractedCompany:
    name: str
    source_url: str
    source_domain: str
    confidence: float
    origin: str


def _url_path_depth(url: str) -> int:
    try:
        path = urlparse(url).path.strip("/")
        return len([p for p in path.split("/") if p]) if path else 0
    except Exception:
        return 99


def is_junk_url(url: str) -> bool:
    low = (url or "").lower()
    return any(part in low for part in _JUNK_DOMAIN_PARTS)


def is_listicle_domain(domain: str) -> bool:
    low = (domain or "").lower()
    return any(ld in low for ld in _LISTICLE_DOMAINS)


def is_blocked_domain(domain: str) -> bool:
    low = (domain or "").lower()
    if is_listicle_domain(low):
        return True
    if any(b in low for b in _BLOCKED_DOMAINS):
        return True
    return any(
        low == b or low.endswith("." + b.split(".")[0] + ".com")
        for b in ("fda.gov", "usp.org", "mdpi.com")
    )


def is_listicle_or_article_title(title: str) -> bool:
    t = (title or "").strip()
    if len(t) < 4:
        return True
    if _PAGE_TITLE_NOT_COMPANY.match(t):
        return True
    if _FASHION_OR_RETAIL_TITLE.search(t):
        return True
    if _LISTICLE_TITLE.search(t):
        return True
    if _QUESTION_TITLE.search(t):
        return True
    if _JUNK_NAME_PATTERN.search(t):
        return True
    words = set(re.findall(r"[a-z]+", t.lower()))
    if words and words <= _GENERIC_TITLE_WORDS:
        return True
    if re.search(r"\b(?:stocks?|income|invest|etf|market\s+cap)\b", t, re.I):
        return True
    if re.search(r"\b(?:in\s+the\s+us|united\s+states only|world\s+in\s+20)\b", t, re.I):
        return True
    # "Category Name (ACRONYM)" pattern → category label, not a company
    if _CATEGORY_ACRONYM_PATTERN.match(t):
        return True
    # Pure industry category term (e.g. "Managed Security Services", "MSSP")
    if _INDUSTRY_CATEGORY_ONLY.match(t):
        return True
    return False


def is_generic_phrase_name(name: str) -> bool:
    """Listicle/category phrases — not operating companies (pattern-based)."""
    t = (name or "").strip()
    if len(t) < 3:
        return True
    low = t.lower()
    if low in _GEO_ONLY_NAMES or low in _VALIDATION_JUNK_EXACT:
        return True
    # Brand + corporate suffix (Sun Pharma, Cipla Laboratories) — not a category phrase
    if _CORPORATE_MARKERS_RE.search(t):
        brand_tokens = [
            w for w in re.findall(r"[A-Za-z]{3,}", t) if w.lower() not in _INDUSTRY_ONLY_WORDS
        ]
        if brand_tokens:
            return False
    if _GENERIC_PHRASE_RE.search(t):
        return True
    if _LEADING_MARKETING_RE.search(t):
        return True
    if _CERTIFICATION_PHRASE_RE.search(t) and not _CORPORATE_MARKERS_RE.search(t):
        return True
    if _ROLE_TAIL_RE.search(t) and not re.search(
        r"\b(?:ltd|limited|inc|corp|corporation)\s*$", t, re.I
    ):
        if not _CORPORATE_MARKERS_RE.search(t) or re.search(
            r"\b(?:distributors?|dealers?|suppliers?|list|lists)\s*$", t, re.I
        ):
            return True
    words = set(re.findall(r"[a-z]{3,}", low))
    if words and words <= _INDUSTRY_ONLY_WORDS:
        return True
    if len(words) >= 2 and not any(
        w not in _INDUSTRY_ONLY_WORDS and len(w) >= 4 for w in words
    ):
        return True
    return False


def looks_like_company_structure(name: str) -> bool:
    """Require a brand token or corporate suffix — blocks pure category phrases."""
    from vendor_intel.discovery.company_registry import is_registry_company

    t = (name or "").strip()
    if not t or is_generic_phrase_name(t):
        return False
    if is_registry_company(t):
        return True
    if _CORPORATE_MARKERS_RE.search(t):
        return True
    words = t.split()
    if len(words) == 1:
        low = t.lower()
        return (
            4 <= len(t) <= 24
            and t[0].isupper()
            and low not in _INDUSTRY_ONLY_WORDS
            and low not in _GEO_ONLY_NAMES
        )
    non_generic = [w for w in words if w.lower() not in _INDUSTRY_ONLY_WORDS]
    return len(non_generic) >= 1 and any(len(w) >= 3 for w in non_generic)


def has_valid_candidate_domain(domain: str, url: str = "") -> bool:
    """Reject directory/blog/listicle domains before keeping a candidate."""
    dom = (domain or "").strip().lower().removeprefix("www.")
    if not dom or len(dom) < 4:
        return False
    if is_blocked_domain(dom) or is_listicle_domain(dom):
        return False
    from vendor_intel.validation.site_kind import is_non_product_site

    if is_non_product_site(dom):
        return False
    if url and (is_junk_url(url) or _url_path_depth(url) > 3):
        return False
    return True


def _is_category_phrase(name: str) -> bool:
    """
    CHANGED: category phrase rejection — generic landscape titles, not companies.
  """
    low = name.lower().strip()

    if low in {
        "india",
        "global",
        "international",
        "worldwide",
        "national",
        "pharmaceutical",
        "pharmaceuticals",
        "pharma",
        "cybersecurity",
        "technology",
        "technologies",
        "software",
        "hardware",
        "digital",
        "usa",
        "uk",
    }:
        return True

    _category_endings = (
        " distributors",
        " dealers",
        " suppliers",
        " importers",
        " exporters",
        " traders",
        " wholesalers",
        " retailers",
        " companies",
        " firms",
        " vendors",
        " providers",
        " manufacturers list",
        " distributors list",
        " companies list",
        " exporters read",
        " importers list",
    )
    if any(low.endswith(e) for e in _category_endings):
        return True

    _generic_starts = (
        "top ",
        "best ",
        "leading ",
        "major ",
        "largest ",
        "list of ",
        "complete list",
        "all ",
        "india ",
        "global ",
        "international ",
    )
    if any(low.startswith(s) for s in _generic_starts):
        if any(low.endswith(e) for e in _category_endings):
            return True

    _role_words = (
        "distributor",
        "supplier",
        "dealer",
        "trader",
        "wholesaler",
        "importer",
        "exporter",
        "retailer",
        "reseller",
    )
    words = low.split()
    if words and words[-1] in _role_words:
        _formal_suffixes = (
            "ltd",
            "limited",
            "pvt",
            "inc",
            "corp",
            "llp",
            "llc",
            "co",
            "gmbh",
            "plc",
        )
        if not any(w in _formal_suffixes for w in words):
            return True

    return False


def is_plausible_company_name(name: str, domain: str = "") -> bool:
    if not name or len(name) < 3:
        return False
    # CHANGED: category phrase rejection
    if _is_category_phrase(name):
        return False
    from vendor_intel.discovery.company_registry import is_blocklisted_domain

    if domain and is_blocklisted_domain(domain):
        return False
    if is_listicle_or_article_title(name) or _JUNK_NAME_PATTERN.search(name):
        return False
    if is_generic_phrase_name(name) or is_generic_category_name(name):
        return False
    if not looks_like_company_structure(name):
        return False
    low = name.lower()
    if low in _GENERIC_TITLE_WORDS or low in _VALIDATION_JUNK_EXACT:
        return False
    if len(name.split()) == 1 and low in ("pharmaceutical", "pharma", "top", "hat"):
        return False
    if domain and is_blocked_domain(domain):
        return False
    return True


_VALIDATION_JUNK_EXACT = frozenset(
    {
        "home",
        "india",
        "pharmaceutical",
        "pharma",
        "b2b pharma",
        "indian pharma",
        "active pharmaceutical",
        "india pharmaceutical",
        "pcd pharma",
        "monopoly pharma",
        "india iqvia",
        "pharmchoices",
        "pharmapproach",
        "python lists",
        "python list",
        "javascript",
        "generic medicines online",
        "cdmo pharma",
        # CHANGED: phase2 quality fix — generic / junk names from search titles
        "smartphone",
        "mobile phones",
        "mobile phone",
        "blogs",
        "february",
        "bing",
        "analyticsinsight",
        "liveindia",
        "pcbasic",
        # Cybersecurity junk — non-firm entities
        "positioniseverything",
        "techbloat",
        "moneyworks4me",
        "companydata",
        "superbcompanies",
        "intellipaat",
        "inventiva",
        "bajajfinserv",
        "seair",
        "ensun",
        "techjockey",
        "cybersecurity channel services",
        "indian softwares",
        "cyber import data india",
        "indian cyber security",
        # Generic category words — these are industry segments, not companies
        "cybersecurity",
        "cyber security",
        "network security",
        "information security",
        "data security",
        "cloud security",
        "endpoint security",
        "managed security services",
        "managed security services (mssp)",
        "managed detection and response",
        "managed detection and response (mdr)",
        "security operations center",
        "security information and event management",
        "extended detection and response",
        "zero trust security",
        "identity and access management",
        "threat intelligence",
        "security orchestration",
        "mssp",
        "mdr",
        "siem",
        "soar",
        "xdr",
        "edr",
        "it security",
        "ot security",
        "iot security",
        # Finance / non-cybersecurity companies mistakenly appearing
        "bajajfinserv",
        "bajaj finserv",
        "paytm",
        "phonepe",
        # Pharma category phrases (not company names)
        "pharmaceutical contract manufacturing",
        "pharma contract manufacturing",
        "contract research companies",
        "contract research organization",
        "contract research map",
        "indian pharma supplier",
        "pharmaceutical products buyers",
        "pharmaceutical products exporters",
        "india you should",
        "pharmchoices the",
        "a complete guide pharmaceutical",
        "identify leading indian pharmaceutical companies",
        "list top pharmaceutical companies in india",
        "find detailed profiles of top indian pharma firms",
        "pharmaceutical and medicine pharmaceutical",
        "oddway international b2b pharmaceutical",
        "the top",
        "iso certified pharma company",
        "pharmaceutical export companies",
        "third party manufacturing pharma",
        "pharma distributors",
        "india market the",
        "dokcare lifesciences explore",
        "pharmahook- india's pharmaceutical",
        "pharmahook india pharmaceutical",
        "who-gmp certified pharma",
        "who gmp certified pharma",
        "trusted supplier",
        "trusted pharma exporter",
        "global supplier",
        "innovative solutions",
        "pharma exporters india",
        "india pharma companies",
        "cybersecurity companies",
        "pharmaceutical manufacturers",
        # CHANGED: full landscape mode — generic pharma/distribution phrases from listicles
        "right pharmaceutical distributor",
        "india pharmaceuticals dealers",
        "medicine wholesale distributors",
        "pharma distributors list",
        "pharmaceutical supplier from",
        "global pharmaceutical supplier",
        "medical equipment suppliers",
        "pharma exporters read",
        "india pharmaceuticals importers",
        "india pharmaceuticals market",
        "india mankind pharma",
        "global scale eskag pharma private limited",
        "top pharmaceutical companies",
        "leading pharmaceutical companies",
        "pharmaceutical companies list",
        "pharma companies in india",
        "list of pharmaceutical companies",
        "pharmaceutical distributors india",
        "pharma distributors india",
        "drug manufacturers india",
        "medicine companies india",
        "pharmaceutical suppliers india",
        "top cybersecurity companies",
        "leading cybersecurity firms",
        "cybersecurity companies list",
        "cybersecurity vendors india",
        "network security companies",
        "top it companies",
        "leading software companies",
        "it companies india",
        "software companies list",
        "usa",
        "uk",
        "read more",
        "view all",
        "see more",
        "click here",
        "learn more",
        "explore",
        "browse",
        "search results",
        "page not found",
    }
)

_GENERIC_CATEGORY_NAME = re.compile(
    r"^(?:the\s+top|top\s+\d+|best\s+\d+)\s*$|"
    r"^(?:pharmaceutical\s+|pharma\s+)?contract\s+(?:manufactur|research)|"
    r"^contract\s+research\s+(?:companies|organization|map)|"
    r"^pharma\s+contract\s+manufacturing|"
    r"^third\s+party\s+manufacturing|"
    r"^pharma\s+distributors?\s*$|"
    r"^pharmaceutical\s+export\s+companies|"
    r"^iso\s+certified\s+pharma|"
    r"^indian\s+pharma\s+supplier|"
    r"^india\s+market\s+the\s*$|"
    r"^pharmaceutical\s+products\s+(?:buyers|exporters)|"
    r"^medicine\s+suppliers(?!\s+[A-Z][a-z]{4,})|"
    r"^india\s+you\s+should|"
    r"^pharmchoices\s+the|"
    r"^a\s+complete\s+guide|"
    r"^updated\s+list|"
    r"^healing\s+discover|"
    r"^outsource\s+accelerator|"
    r"^identify\s+leading|"
    r"^list\s+top|"
    r"^find\s+(?:detailed\s+)?profiles|"
    r"^pharmaceutical\s+and\s+medicine\b|"
    r"^pharmahook[\s-]*india|"
    r"\bexplore\s*$|"
    r"\s+the\s*$",
    re.I,
)

_CORPORATE_SUFFIX_IN_NAME = re.compile(
    r"\b(?:laboratories|laboratory|limited|ltd\.?|inc\.?|industries|healthcare|"
    r"pharmaceuticals?|pharma|corp\.?|corporation|group|company)\b",
    re.I,
)

_VALIDATION_JUNK_RE = re.compile(
    r"\b(?:directory|marketplace|roadmap|supplier\s+directory|thirdparty|"
    r"healing the world|companies the|alliance representing|leading b2b|"
    r"global healthcare solutions|pharmaceutical platform|b2b pharmaceutical platform|"
    r"find thirdparty|pioneering global|buy\s+|shop\s+|online\s+in\s+india|"
    r"news\s+headlines|latest\s+news|insights\s+on\s+the|times\s+top|"
    r"women'?s\s+tops?|pharma\s+franchise|pcd\s+pharma|test\s+ranking|"
    r"products\s+limited$|large-scale\s+pharma$|python\s+lists?|javascript|"
    r"tutorial|w3schools|geeksforgeeks|contract\s+manufacturing\s+in\s+pharma$|"
    r"^cdmo\s*&|b2b\s+pharmaceutical$|oddway\s+international\s+b2b)\b",
    re.I,
)


def is_generic_category_name(name: str) -> bool:
    """Industry category phrases extracted from listicles — not operating companies."""
    if is_generic_phrase_name(name):
        return True
    t = (name or "").strip()
    if len(t) < 6:
        return False
    if _GENERIC_CATEGORY_NAME.search(t):
        return True
    if _GENERIC_CATEGORY_NAME.search(t.lower()):
        return True
    low = t.lower()
    if low in _VALIDATION_JUNK_EXACT:
        return True
    # Pure role phrase: "Contract Manufacturing" with no corporate suffix / brand token
    if re.search(r"\bcontract\s+(?:manufactur|research)\b", low, re.I):
        if not _CORPORATE_SUFFIX_IN_NAME.search(t) and not re.search(
            r"\b(?:dr|sun|cipla|lupin|aurobindo|torrent|divis|cadila)\b", low, re.I
        ):
            return True
    words = set(re.findall(r"[a-z]{3,}", low))
    role_only = {
        "pharmaceutical", "pharma", "contract", "manufacturing", "research",
        "companies", "company", "organization", "supplier", "products",
        "buyers", "exporters", "indian", "india", "international", "b2b",
        "medicine", "suppliers", "healthcare", "guide", "complete",
    }
    if words and words <= role_only:
        return True
    # "ISO Certified ...", "Export Companies", "Third Party Manufacturing"
    if re.search(
        r"\b(?:iso\s+certified|export\s+companies|third\s+party\s+manufacturing|"
        r"certified\s+pharma\s+company|market\s+the)\b",
        low,
        re.I,
    ):
        if not re.search(
            r"\b(?:sun|cipla|lupin|aurobindo|torrent|divis|cadila|dr\.?\s*reddy)\b",
            low,
            re.I,
        ):
            return True
    if re.fullmatch(r"the\s+top", low):
        return True
    if low.endswith(" the") or low.endswith(" explore"):
        return True
    return False


_PRE_SCRAPE_NAME_JUNK = re.compile(
    r"\b(?:list|suppliers?|dealers?|import|export|directory|portal|marketplace)\b",
    re.I,
)
_PRE_SCRAPE_DOMAIN_JUNK = re.compile(
    r"(?:^|[.-])(?:mart|b2b|directory|portal|indiamart|tradeindia)(?:[.-]|$)",
    re.I,
)


def is_valid_company(
    name: str,
    domain: str = "",
    *,
    scope: dict[str, Any] | None = None,
) -> bool:
    """Pre-scrape gate — reject generic phrases and junk domains before website fetch."""
    from vendor_intel.discovery.company_registry import is_registry_company

    n = (name or "").strip()
    if not n:
        return False
    if is_registry_company(n, scope):
        return True
    if _PRE_SCRAPE_NAME_JUNK.search(n):
        return False
    if is_generic_phrase_name(n):
        return False
    if not is_plausible_company_name(n, domain):
        return False
    if not looks_like_company_structure(n):
        return False
    words = n.split()
    if len(words) < 2 and not (
        len(words) == 1 and domain and len(words[0]) >= 4 and not is_blocked_domain(domain)
    ):
        return False
    dom = (domain or "").strip().lower()
    if not dom or not has_valid_candidate_domain(dom):
        return False
    if _PRE_SCRAPE_DOMAIN_JUNK.search(dom) or is_listicle_domain(dom):
        return False
    return True


def is_likely_real_company_name(name: str, domain: str = "") -> bool:
    """Strict gate for Tier B+ export — must look like a real operating company."""
    if not is_plausible_company_name(name, domain):
        return False
    if is_generic_category_name(name):
        return False
    from vendor_intel.discovery.company_registry import is_registry_company
    from vendor_intel.validation.site_kind import is_article_title_name

    if is_article_title_name(name):
        return False
    if is_registry_company(name):
        return True
    # Short brand names (Cipla, Lupin, Akums) — valid if domain present
    words = name.split()
    if (
        len(words) == 1
        and 4 <= len(name) <= 24
        and domain
        and not is_blocked_domain(domain)
    ):
        return True
    if _CORPORATE_SUFFIX_IN_NAME.search(name):
        return is_validation_ready_name(name, domain)
    return is_validation_ready_name(name, domain)


def is_validation_ready_name(name: str, domain: str = "") -> bool:
    """Stricter than discovery plausibility — used in Phase 3 tiering."""
    from vendor_intel.discovery.company_registry import is_blocklisted_domain
    from vendor_intel.validation.site_kind import (
        is_article_title_name,
        is_non_product_site,
        name_domain_mismatch,
    )

    if not is_plausible_company_name(name, domain):
        return False
    if is_article_title_name(name):
        return False
    if domain and is_non_product_site(domain, name=name):
        return False
    if domain and name_domain_mismatch(name, domain):
        return False
    low = name.lower().strip()
    if is_generic_category_name(name):
        return False
    if low in _VALIDATION_JUNK_EXACT:
        return False
    if _VALIDATION_JUNK_RE.search(name):
        return False
    if len(name) > 52:
        return False
    if domain and (is_listicle_domain(domain) or is_blocklisted_domain(domain)):
        return False
    words = name.split()
    if len(words) >= 6 and not re.search(
        r"\b(?:ltd|limited|laboratories|pharma(?:ceuticals)?|industries|healthcare|corp|llp)\b",
        name,
        re.I,
    ):
        return False
    if len(words) == 1 and low in ("laafon", "apromart"):
        return False
    return True


def domain_to_brand_name(domain: str) -> str:
    if not domain or is_blocked_domain(domain):
        return ""
    base = domain.lower().split(".")[0]
    if base in ("www", "m", "en", "top", "tops"):
        return ""
    spaced = re.sub(r"([a-z])(pharma|pharm|lab|bio)", r"\1 \2", base, flags=re.I)
    spaced = spaced.replace("-", " ").replace("_", " ")
    _keep_suffix = frozenset({"pharma", "pharm", "labs", "lab", "bio", "tech", "security"})
    words = [
        w.capitalize()
        for w in spaced.split()
        if w and (w.lower() not in _GENERIC_TITLE_WORDS or w.lower() in _keep_suffix)
    ]
    if not words:
        return ""
    name = " ".join(words)
    if name.lower().endswith("pharma") and " " not in name[:-5]:
        core = name[:-5].strip()
        if core:
            return f"{core} Pharma"
    return name if is_plausible_company_name(name, domain) else ""


def looks_like_company_site(url: str, domain: str, title: str) -> bool:
    if is_junk_url(url) or is_blocked_domain(domain):
        return False
    if not domain or len(domain) < 4:
        return False
    if _url_path_depth(url) > 2:
        return False
    low_dom = domain.lower()
    if any(
        x in low_dom
        for x in (
            "wikipedia.",
            "linkedin.",
            "youtube.",
            "facebook.",
            "news.",
            "blog.",
            "medium.",
            "statista.",
            "mdpi.",
            "fda.",
            "usp.",
        )
    ):
        return False
    base = low_dom.split(".")[0]
    if base in _GENERIC_TITLE_WORDS or base in ("top", "tops", "wiki"):
        return False
    if "journal" in (title or "").lower():
        return False
    if is_listicle_domain(domain):
        return False
    if is_listicle_or_article_title(title):
        return False
    return True


def extract_names_from_text(text: str, *, max_names: int = 35) -> list[str]:
    from vendor_intel.discovery.entity_scoring import is_bad_phrase

    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for pat in _COMPANY_PATTERNS:
        for m in pat.finditer(text):
            name = normalize_name(m.group(1).strip())
            key = name.lower()
            if len(name) < 3 or key in seen:
                continue
            if is_bad_phrase(name):
                continue
            if not is_plausible_company_name(name):
                continue
            if not looks_like_company_structure(name):
                continue
            seen.add(key)
            found.append(name)
            if len(found) >= max_names:
                return found
    return found


def clean_title_as_name(title: str) -> str:
    t = (title or "").strip()
    m = _OFFICIAL_SITE_TITLE.match(t)
    if m:
        t = m.group(1).strip()
    for sep in ("|", " – ", " — ", " - "):
        if sep in t:
            left = t.split(sep)[0].strip()
            if len(left) >= 4 and not is_listicle_or_article_title(left):
                t = left
                break
    t = re.sub(r"\s*[\.\…]+\s*$", "", t).strip()
    name = normalize_name(t[:80])
    return name if is_plausible_company_name(name) else ""


def _geo_ok(blob: str, geo: str) -> bool:
    geo_low = (geo or "").lower()
    if not geo_low or geo_low == "global":
        return True
    if geo_low in blob or "indian" in blob and geo_low == "india":
        return True
    parts = [p.strip().lower() for p in geo.split(",") if len(p.strip()) >= 3]
    return any(p in blob for p in parts)


def hits_from_search_result(
    title: str,
    link: str,
    snippet: str,
    *,
    prompt_id: str,
    funnel_level: str,
    backend: str,
    search_theme: str,
    scope: dict[str, Any],
) -> list[ExtractedCompany]:
    from vendor_intel.discovery.entity_scoring import finalize_entity_name, is_bad_phrase

    domain = domain_from_url(link)
    if is_junk_url(link) or is_blocked_domain(domain):
        return []

    geo = (scope.get("geographies") or ["global"])[0]
    blob = f"{title} {snippet} {link}".lower()

    if geo.lower() not in ("", "global") and not _geo_ok(blob, geo):
        if re.search(r"\b(?:united\s+states|in\s+the\s+us|u\.s\.)\b", blob):
            if "india" not in blob and "indian" not in blob:
                # Still parse listicles for company names inside India-focused snippets
                if not is_listicle_or_article_title(title):
                    return []
        elif not _geo_ok(blob, geo):
            pass  # allow listicle mining below

    out: list[ExtractedCompany] = []
    combined = f"{title} {snippet}"

    # Always mine listicle/snippet text for multiple company names first
    if is_listicle_or_article_title(title) or len(extract_names_from_text(combined, max_names=5)) >= 2:
        for name in extract_names_from_text(combined, max_names=40):
            if is_bad_phrase(name):
                continue
            out.append(
                ExtractedCompany(
                    name=name,
                    source_url=link,
                    source_domain=domain,
                    confidence=0.6,
                    origin="listicle_snippet",
                )
            )
        if out:
            return out

    if looks_like_company_site(link, domain, title):
        raw_name = clean_title_as_name(title) or domain_to_brand_name(domain)
        name = finalize_entity_name(raw_name, domain) if raw_name else ""
        if name and is_plausible_company_name(name, domain):
            out.append(
                ExtractedCompany(
                    name=name,
                    source_url=link,
                    source_domain=domain,
                    confidence=0.9,
                    origin="company_site",
                )
            )
        return out

    if is_listicle_domain(domain) or is_blocked_domain(domain):
        for n in extract_names_from_text(snippet, max_names=15):
            out.append(
                ExtractedCompany(
                    name=n,
                    source_url=link,
                    source_domain=domain,
                    confidence=0.5,
                    origin="snippet",
                )
            )
        return out

    name = clean_title_as_name(title)
    if name and is_plausible_company_name(name, domain):
        out.append(
            ExtractedCompany(
                name=name,
                source_url=link,
                source_domain=domain,
                confidence=0.45,
                origin="title",
            )
        )

    for n in extract_names_from_text(snippet, max_names=15):
        if n.lower() != (name or "").lower():
            out.append(
                ExtractedCompany(
                    name=n,
                    source_url=link,
                    source_domain=domain,
                    confidence=0.5,
                    origin="snippet",
                )
            )
    return out


def to_discovery_hits(
    extracted: list[ExtractedCompany],
    *,
    prompt_id: str,
    funnel_level: str,
    backend: str,
    snippet: str,
    search_theme: str,
    scope: dict[str, Any] | None = None,
) -> list[DiscoveryHit]:
    from vendor_intel.discovery.entity_scoring import (
        finalize_entity_name,
        is_bad_phrase,
        passes_entity_score,
        score_entity_candidate,
    )

    hits: list[DiscoveryHit] = []
    seen: set[str] = set()
    occurrence: dict[str, int] = {}
    for ex in extracted:
        dom = ex.source_domain or ""
        if is_bad_phrase(ex.name):
            continue
        resolved = finalize_entity_name(ex.name, dom)
        if not resolved:
            continue
        key = resolved.lower()
        occurrence[key] = occurrence.get(key, 0) + 1

    for ex in extracted:
        dom = ex.source_domain or ""
        if is_bad_phrase(ex.name):
            continue
        name = finalize_entity_name(ex.name, dom)
        if not name:
            continue
        key = name.lower()
        if ex.confidence < 0.4 or key in seen:
            continue
        if not is_plausible_company_name(name, dom):
            continue
        if dom and not has_valid_candidate_domain(dom, ex.source_url):
            continue
        escore = score_entity_candidate(
            name,
            dom,
            origin=ex.origin,
            occurrence_count=occurrence.get(key, 1),
            scope=scope,
        )
        if not passes_entity_score(escore, origin=ex.origin):
            continue
        if not is_valid_company(name, dom, scope=scope):
            continue
        seen.add(key)
        hits.append(
            DiscoveryHit(
                name_raw=name,
                source_url=ex.source_url,
                source_domain=ex.source_domain,
                prompt_id=prompt_id,
                backend=backend,
                snippet=snippet[:500],
                funnel_level=funnel_level,
                search_theme=search_theme,
            )
        )
    return hits


def pick_primary_domain(hits: list[DiscoveryHit], canonical_name: str) -> str:
    from vendor_intel.discovery.company_registry import (
        is_blocklisted_domain,
        resolve_official_domain,
    )

    hit_domains = [
        h.source_domain or domain_from_url(h.source_url)
        for h in hits
        if h.source_domain or h.source_url
    ]
    resolved = resolve_official_domain(canonical_name, hit_domains)
    if resolved:
        return resolved

    name_low = canonical_name.lower()
    name_compact = name_low.replace(" ", "").replace(".", "")
    scored: list[tuple[int, str]] = []
    for h in hits:
        dom = h.source_domain or domain_from_url(h.source_url)
        if not dom or is_blocked_domain(dom):
            continue
        score = 0
        dom_compact = dom.replace("-", "").replace(".", "")
        if looks_like_company_site(h.source_url, dom, h.name_raw):
            score += 10
        if name_compact and name_compact in dom_compact:
            score += 12
        elif name_low.split()[0] in dom_compact and len(name_low.split()[0]) >= 4:
            score += 6
        if is_listicle_domain(dom) or is_blocklisted_domain(dom):
            score -= 50
        if is_junk_url(h.source_url):
            score -= 20
        score -= _url_path_depth(h.source_url)
        if score > 0:
            scored.append((score, dom))
    if not scored:
        return ""
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]
