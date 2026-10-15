"""Source-quality filter shared by both search paths (`field_agent.run_field_group`
and `cmi_report/run_data.search`).

Snippets from video, social, marketplace, Q&A, horoscope and throwaway-TLD
link-farm sites were being used to size markets (FINDINGS-2026-10-15 X5:
yandex, vkvideo, youtube, linkedin, hepsiburada, astrohelper.ru, genera.so,
koji.so, spa.so). `filter_sources` drops them before any LLM sees them, and
`quality` scores what is left so thin evidence can be flagged
(`_sources.low_evidence`, used by PLAN-02 and PLAN-08).

`on_topic` then keeps only snippets that mention the market itself, so a
degraded search (dictionary or word-game pages) never reaches the LLM.

A snippet is a dict from `gather_with_sources`: {"text", "url", "domain", ...}.
"""
from __future__ import annotations

import re

# Exact domains (a subdomain such as m.youtube.com matches too). An entry
# ending in ".*" matches that name under any TLD (yandex.ru, market.yandex.com.tr).
BLOCKED_DOMAINS: set[str] = {
    # video
    "youtube.com", "youtu.be", "vkvideo.ru", "rutube.ru", "dailymotion.com", "vimeo.com",
    # social
    "vk.com", "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com",
    "tiktok.com", "pinterest.*", "threads.net", "ok.ru", "telegram.org", "t.me",
    # search portals
    "yandex.*",
    # marketplaces
    "hepsiburada.com", "amazon.*", "ebay.*", "aliexpress.*", "alibaba.com", "indiamart.com",
    "ozon.ru", "wildberries.ru", "trendyol.com", "flipkart.com",
    # Q&A, forums, document dumps
    "reddit.com", "quora.com", "scribd.com", "answers.com", "brainly.com", "slideshare.net",
    # horoscope / junk
    "astrohelper.ru",
}

# Throwaway TLDs used by link farms (genera.so, koji.so, spa.so ...), unless
# the exact domain is allow-listed below.
_LINK_FARM_TLD_RE = re.compile(r"\.(so|tk|xyz|ml|ga|cf|gq)$", re.I)
LINK_FARM_ALLOW: set[str] = set()

# Hints that a domain is a trusted kind of source. A domain is trusted when it
# matches any of these.
TRUSTED_HINTS: dict[str, set[str]] = {
    # intergovernmental and government (".gov", ".gov.xx", "gouv", "gob" handled in _is_government)
    "government": {"europa.eu", "fao.org", "who.int", "worldbank.org", "oecd.org", "wto.org", "un.org",
                   "imf.org", "unctad.org", "usda.gov", "ec.europa.eu"},
    "press_release": {"prnewswire.com", "businesswire.com", "globenewswire.com", "einpresswire.com",
                      "accesswire.com", "newswire.ca"},
    "research_publisher": {"precedenceresearch.com", "grandviewresearch.com", "marketsandmarkets.com",
                           "mordorintelligence.com", "fortunebusinessinsights.com", "imarcgroup.com",
                           "alliedmarketresearch.com", "verifiedmarketresearch.com", "market.us",
                           "statista.com"},
    "news": {"reuters.com", "bloomberg.com", "ft.com", "wsj.com", "cnbc.com", "economist.com",
             "apnews.com", "nikkei.com", "zawya.com", "arabnews.com", "thenationalnews.com",
             # trade press
             "agropages.com", "feednavigator.com", "nutraingredients.com", "foodnavigator.com",
             "wattagnet.com", "wattpoultry.com", "thepoultrysite.com", "thefishsite.com",
             "seafoodsource.com", "undercurrentnews.com", "globalseafood.org", "agfundernews.com",
             "farmprogress.com", "agriculture.com", "world-grain.com", "feedstrategy.com",
             "allaboutfeed.net", "fooddive.com", "chemanalyst.com", "icis.com", "techcrunch.com"},
}
# Company investor-relations / newsroom subdomains (ir.basf.com, investors.adm.com, newsroom.x.com).
_IR_RE = re.compile(r"^(ir|investor|investors|investor-relations|newsroom|press)\.", re.I)
# Trade associations, federations and councils.
_ASSOCIATION_RE = re.compile(r"(association|federation|council|assoc\b|-assn|\bassn\b)", re.I)

# Groups whose numbers size the market; thin evidence here is flagged.
NUMERIC_GROUPS: set[str] = {"market_size", "regional", "competitive_landscape", "vendors",
                            "segmentation_grade_form"}
_NUMERIC_PREFIXES = ("countries_", "segments_")
MIN_KEPT_SNIPPETS = 3


def _norm_domain(domain: str) -> str:
    d = (domain or "").strip().lower()
    d = d.split("//")[-1].split("/")[0].split(":")[0]
    return d.removeprefix("www.")


def _matches(domain: str, entry: str) -> bool:
    """`domain` is `entry` or a subdomain of it; an entry ending in ".*"
    matches its name under any TLD."""
    if entry.endswith(".*"):
        name = entry[:-2]
        labels = domain.split(".")
        return name in labels[:-1]
    return domain == entry or domain.endswith("." + entry)


def block_reason(domain: str) -> str | None:
    """Why a domain is blocked, or None if it isn't."""
    d = _norm_domain(domain)
    if not d:
        return None
    for entry in BLOCKED_DOMAINS:
        if _matches(d, entry):
            return f"blocked domain ({entry})"
    if _LINK_FARM_TLD_RE.search(d) and d not in LINK_FARM_ALLOW:
        return f"link-farm TLD (.{d.rsplit('.', 1)[-1]})"
    return None


def _is_government(domain: str) -> bool:
    """.gov / .int / .mil, or a government second level such as gov.uk,
    moa.gov.in, agriculture.gouv.fr, gob.mx, maff.go.jp."""
    labels = domain.split(".")
    if labels[-1] in ("gov", "int", "mil"):
        return True
    return any(lab in ("gov", "gouv", "gob", "govt") for lab in labels[:-1]) or (
        len(labels) >= 3 and labels[-2] == "go")


def trust_kind(domain: str) -> str | None:
    """The kind of trusted source a domain is ("government", "news" ...), or None."""
    d = _norm_domain(domain)
    if not d or block_reason(d):
        return None
    if _is_government(d):
        return "government"
    for kind, entries in TRUSTED_HINTS.items():
        if any(_matches(d, e) for e in entries):
            return kind
    if _IR_RE.search(d):
        return "company_ir"
    if _ASSOCIATION_RE.search(d):
        return "trade_association"
    return None


def filter_sources(sourced: list[dict]) -> tuple[list[dict], list[dict]]:
    """Splits snippets into (kept, dropped). Each dropped item is
    {"domain", "url", "reason"}."""
    kept: list[dict] = []
    dropped: list[dict] = []
    for s in sourced or []:
        domain = s.get("domain") or _norm_domain(s.get("url") or "")
        why = block_reason(domain)
        if why:
            dropped.append({"domain": _norm_domain(domain), "url": s.get("url"), "reason": why})
        else:
            kept.append(s)
    return kept, dropped


def quality(sourced: list[dict]) -> dict:
    """{"n", "distinct_domains", "trusted", "score"}. `trusted` counts distinct
    trusted domains; `score` (0-1) weighs snippet count, domain diversity and,
    most, trusted domains."""
    domains = {_norm_domain(s.get("domain") or s.get("url") or "") for s in sourced or []} - {""}
    trusted = sum(1 for d in domains if trust_kind(d))
    n = len(sourced or [])
    score = 0.3 * min(n / 10, 1) + 0.3 * min(len(domains) / 6, 1) + 0.4 * min(trusted / 3, 1)
    return {"n": n, "distinct_domains": len(domains), "trusted": trusted, "score": round(score, 2)}


def is_numeric_group(key: str) -> bool:
    return key in NUMERIC_GROUPS or key.startswith(_NUMERIC_PREFIXES)


_GENERIC = {"market", "global", "the", "and", "for", "industry", "worldwide"}


def market_tokens(market: str) -> set[str]:
    """Stems of the market name's own words ('agricultural biostimulants' ->
    {'agricul', 'biostim'}) used to recognise on-topic snippets."""
    return {w[:7] for w in re.findall(r"[a-z]+", market.lower()) if len(w) >= 4 and w not in _GENERIC}


def on_topic(snippets: list[dict], market: str) -> tuple[list[dict], list[dict]]:
    """Splits snippets into (on_topic, off_topic): a snippet is on topic when
    its text mentions a stem of the market name. A market name with no usable
    stem keeps everything (nothing to test against)."""
    stems = market_tokens(market)
    if not stems:
        return list(snippets or []), []
    kept, off = [], []
    for s in snippets or []:
        (kept if any(st in (s.get("text") or "").lower() for st in stems) else off).append(s)
    return kept, off


def sources_block(key: str, kept: list[dict], dropped: list[dict], off_topic_dropped: int = 0) -> dict:
    """The `_sources` block stored on every group:
    {"num_sources", "domains", "dropped_domains", "off_topic_dropped", "quality"}
    plus `low_evidence: true` for a numeric group with < 3 kept snippets or no
    trusted domain."""
    q = quality(kept)
    block = {
        "num_sources": len(kept),
        "domains": sorted({s["domain"] for s in kept if s.get("domain")}),
        "dropped_domains": sorted({d["domain"] for d in dropped if d.get("domain")}),
        "off_topic_dropped": off_topic_dropped,
        "quality": q,
    }
    if is_numeric_group(key) and (q["n"] < MIN_KEPT_SNIPPETS or q["trusted"] == 0):
        block["low_evidence"] = True
    return block
